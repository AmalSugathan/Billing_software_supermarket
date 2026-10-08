import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { ApiError, request, type Business, type Session, type Store } from './identity-api';
import { loadPending, persistPending } from './commerce-api';
import { ocr, uploadInvoice, type InvoiceDocument, type OcrCandidate, type OcrProvider, type PurchaseSource, type InvoiceDraft } from './ocr-api';
import type { Supplier } from './operations-api';
const message = (error: unknown) => error instanceof Error ? error.message : 'The invoice action could not be completed.';

export default function InvoiceInbox({ business, session, stores, suppliers, onPurchase }: { business: Business; session: Session; stores: Store[]; suppliers: Supplier[]; onPurchase?: (source: PurchaseSource) => void }) {
  const [storeId, setStoreId] = useState(stores[0]?.id ?? '');
  const [documents, setDocuments] = useState<InvoiceDocument[]>([]);
  const [provider, setProvider] = useState<OcrProvider | null>(null);
  const [selected, setSelected] = useState<InvoiceDocument | null>(null);
  const [preview, setPreview] = useState('');
  const [draft, setDraft] = useState<InvoiceDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [candidates, setCandidates] = useState<OcrCandidate[]>([]);
  const [query, setQuery] = useState('');
  const [supplierId, setSupplierId] = useState('');
  const [productId, setProductId] = useState('');
  const [offset, setOffset] = useState(0);
  const route = '/businesses/' + business.id + '/stores/' + storeId + '/ocr-documents';
  const recovery = 'invoice-intake:' + session.user.id + ':' + business.id + ':' + storeId;
  const canApprove = business.capabilities.includes('actions.approve');
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const [items, availability] = await Promise.all([request(route + '?offset=' + offset, ocr.documents, { signal }), request(route + '/provider', ocr.provider, { signal })]);
    return { items, availability };
  }, [route, offset]);
  useEffect(() => {
    if (!storeId) return;
    const controller = new AbortController();
    void refresh(controller.signal).then((data) => { if (!controller.signal.aborted) { setDocuments(data.items); setProvider(data.availability); } }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(message(cause)); });
    return () => controller.abort();
  }, [refresh, storeId]);
  const selectedId = selected?.id;
  const selectedStatus = selected?.status;
  useEffect(() => {
    if (!selectedId) return;
    const controller = new AbortController();
    let url = '';
    void fetch('/api/v1' + route + '/' + selectedId + '/content', { credentials: 'same-origin', cache: 'no-store', signal: controller.signal }).then(async (response) => {
      if (!response.ok) throw new Error('Invoice preview is unavailable.');
      const content = await response.blob();
      if (!controller.signal.aborted && content.type.startsWith('image/')) { url = URL.createObjectURL(content); setPreview(url); }
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(message(cause)); });
    return () => { controller.abort(); if (url) URL.revokeObjectURL(url); };
  }, [route, selectedId]);
  useEffect(() => {
    if (!selectedId || selectedStatus !== 'processing') return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = () => {
      void request(route + '/' + selectedId, ocr.document, { signal: controller.signal }).then((result) => {
        if (!controller.signal.aborted) {
          setSelected(result);
          if (result.status === 'processing') timer = setTimeout(poll, 3000);
          else { localStorage.removeItem(recovery + ':process:' + selectedId); setNotice('OCR status: ' + result.status.replaceAll('_', ' ') + '. No purchase was posted.'); }
        }
      }).catch((cause: unknown) => { if (!controller.signal.aborted) { setError(message(cause)); timer = setTimeout(poll, 10000); } });
    };
    timer = setTimeout(poll, 1000);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [route, selectedId, selectedStatus, recovery]);
  async function action(work: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('');
    try { await work(); } catch (cause) { setError(message(cause)); } finally { setBusy(false); }
  }
  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const data = new FormData(form); const file = data.get('invoice');
    if (!(file instanceof File)) return;
    await action(async () => {
      const saved = await uploadInvoice(route, file, String(data.get('origin')), session.csrf_token, recovery + ':upload');
      setPreview(''); setDraft(null); setSelected(saved); setCandidates([]); setProductId(''); const refreshed = await refresh(); setDocuments(refreshed.items); setProvider(refreshed.availability); form.reset(); setNotice('Invoice saved privately. Stock and accounts have not changed.');
    });
  }
  async function process() {
    if (!selected) return;
    await action(async () => {
      const storageKey = recovery + ':process:' + selected.id;
      const command = loadPending(storageKey) ?? { route: route + '/' + selected.id + '/process', key: crypto.randomUUID(), body: { reason: 'User requested private invoice OCR review' } };
      persistPending(storageKey, command);
      const result = await request(command.route, ocr.document, { method: 'POST', csrf: session.csrf_token, idempotencyKey: command.key, body: command.body, timeoutMs: 15000 });
      if (result.status === 'review_required' || result.status === 'failed' || result.status === 'retry_due') localStorage.removeItem(storageKey);
      setDraft(null); setSelected(result); const refreshed = await refresh(); setDocuments(refreshed.items); setProvider(refreshed.availability);
      setNotice(result.status === 'review_required' ? 'OCR evidence is ready for human review. No purchase was posted.' : 'OCR status: ' + result.status + '. Original evidence is retained.');
    });
  }
  async function approveMapping(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!selected) return;
    const data = new FormData(event.currentTarget);
    await action(async () => {
      const storageKey = recovery + ':mapping:' + selected.id;
      const body = { supplier_id: supplierId, product_id: productId, supplier_description: query, reason: String(data.get('reason')), confirmed: true };
      const previous = loadPending(storageKey);
      if (previous && JSON.stringify(previous.body) !== JSON.stringify(body)) throw new Error('Retry the pending mapping with the same details first.');
      const command = previous ?? { route: route + '/' + selected.id + '/mappings', key: crypto.randomUUID(), body };
      persistPending(storageKey, command);
      try { await request(command.route, ocr.mapping, { method: 'POST', csrf: session.csrf_token, idempotencyKey: command.key, body: command.body }); }
      catch (cause) { if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500) localStorage.removeItem(storageKey); throw cause; }
      localStorage.removeItem(storageKey); setNotice('Supplier description mapping approved. Stock has not changed.');
    });
  }
  return <section className="workspace-card">
    <h4>Purchase invoice inbox</h4>
    <p>Save the bill, inspect the source and review OCR evidence. OCR prepares proposals; purchases update stock and supplier payables only after you review and confirm them.</p>
    <label>Store<select value={storeId} disabled={busy} onChange={(event) => { setStoreId(event.target.value); setSelected(null); setDraft(null); setPreview(''); setCandidates([]); setProductId(''); setOffset(0); setError(''); }}>{stores.map((store) => <option key={store.id} value={store.id}>{store.name}</option>)}</select></label>
    {provider && <p role="status">{provider.message} Model: {provider.provider_model}. Semantic matching unavailable.</p>}
    {provider && !provider.upload_enabled && <p>Private document encryption must be configured before uploads are available.</p>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <form onSubmit={(event) => { void upload(event); }}>
      <label>Invoice image or PDF<input name="invoice" type="file" accept="image/jpeg,image/png,application/pdf" required disabled={busy || !provider?.upload_enabled} /></label>
      <label>Evidence origin<select name="origin" defaultValue="authentic"><option value="authentic">Original supplier bill</option><option value="synthetic">Synthetic practice bill</option><option value="unknown">Unknown origin</option></select></label>
      <p className="hint">Up to 10 MiB and 10 PDF pages. An uncertain upload must be retried with the same file and origin; no invoice file is kept in browser recovery storage.</p>
      <button disabled={busy || !provider?.upload_enabled}>Save invoice privately</button>
    </form>
    <button disabled={busy} onClick={() => { void action(async () => { const refreshed = await refresh(); setDocuments(refreshed.items); setProvider(refreshed.availability); }); }}>Refresh inbox</button>
    <ul>{documents.map((document) => <li key={document.id}><button className="secondary-button" disabled={busy} onClick={() => { void action(async () => { if (selectedId !== document.id) setPreview(''); setCandidates([]); setDraft(null); setProductId(''); setSelected(await request(route + '/' + document.id, ocr.document)); }); }}>{document.filename} / {document.status.replaceAll('_', ' ')} / {document.data_origin}</button></li>)}</ul>
    <button disabled={busy || offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>Previous invoices</button>
    <button disabled={busy || documents.length < 50} onClick={() => setOffset(offset + 50)}>Next invoices</button>
    {selected && <>
      <h4>{selected.filename}</h4><p>Status: {selected.status.replaceAll('_', ' ')}. {selected.page_count} page(s). {selected.error_code && 'Processing issue: ' + selected.error_code}</p>
      <a href={'/api/v1' + route + '/' + selected.id + '/content?original=true'} download>Download original bill</a>
      {preview && <img src={preview} alt="Supplier invoice source for human review" className="invoice-source-preview" />}
      <button disabled={busy || !provider?.provider_configured || selected.status === 'processing'} onClick={() => { void process(); }}>Run private OCR</button>
      <p className="hint">OCR confidence is unavailable from this adapter. Every extracted value requires review. OCR never changes stock, selling prices or payments.</p>
      {selected.evidence?.pages.map((page) => <section key={page.page_number}><h5>OCR page {page.page_number}: unreviewed evidence</h5><pre className="invoice-ocr-evidence">{page.markdown || page.blocks.map((block) => block.text).join('\n')}</pre></section>)}
      {selected.status === 'review_required' && <button disabled={busy} onClick={() => { void action(async () => { setDraft(await request(route + '/' + selected.id + '/draft', ocr.draft)); }); }}>Prepare invoice fields for review</button>}
      {draft && draft.document_id === selected.id && <section>
        <h4>Proposed purchase fields: review required</h4>
        <ul>{draft.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
        <dl>{Object.entries(draft.fields).map(([name, field]) => <div key={name}><dt>{name.replaceAll('_', ' ')}</dt><dd>{field.value ?? 'Unavailable or ambiguous'} / confidence unavailable</dd></div>)}</dl>
        <p>Recognized line-total sum: {draft.line_total_sum ?? 'Unavailable'}. Compare every row with the original bill, including carton/piece conversions and free quantities.</p>
        {draft.rows.map((row, index) => <div className="table-scroll" key={index}><p>Source page {row.page}, block {row.block}, row {row.row}</p><table aria-label={'Proposed invoice row ' + (index + 1)}><thead><tr>{row.headers.map((header, column) => <th key={column}>{header}</th>)}</tr></thead><tbody><tr>{row.cells.map((cell, column) => <td key={column}>{cell}</td>)}</tr></tbody></table></div>)}
        {onPurchase && <button disabled={busy || !draft.rows.length} onClick={() => onPurchase({ draft, storeId, filename: selected.filename })}>Review as purchase</button>}
        <p>These fields do not authorize stock or accounting changes. Approved purchase entry is still required, and supplier payment status is unknown.</p>
      </section>}
      <h4>Find an existing product</h4><p className="hint">Name and pack-size matching searches up to 2,000 active products. Scores are similarity scores, not AI confidence. Verify the pack size and unit against the bill.</p>
      <label>Supplier description<input value={query} maxLength={250} onChange={(event) => { setQuery(event.target.value); setCandidates([]); setProductId(''); }} /></label>
      <label>Supplier<select value={supplierId} onChange={(event) => { setSupplierId(event.target.value); setCandidates([]); setProductId(''); }}><option value="">Select a supplier</option>{suppliers.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.name}</option>)}</select></label>
      <button disabled={busy || query.trim().length < 2} onClick={() => { void action(async () => { setCandidates(await request(route + '/' + selected.id + '/matches?q=' + encodeURIComponent(query) + (supplierId ? '&supplier_id=' + supplierId : ''), ocr.candidates)); }); }}>Find candidates</button>
      <ul>{candidates.map((candidate) => <li key={candidate.product_id}><label><input type="radio" name="match" checked={productId === candidate.product_id} onChange={() => setProductId(candidate.product_id)} />{candidate.name} / {candidate.sku} / {candidate.unit} / score {candidate.match_score} / {candidate.method.replaceAll('_', ' ')}</label></li>)}</ul>
      {canApprove && <form onSubmit={(event) => { void approveMapping(event); }}><label>Verification reason<input name="reason" minLength={3} maxLength={500} required /></label><label><input type="checkbox" required />I checked the product, pack size and unit against this invoice.</label><button disabled={busy || !supplierId || !productId}>Approve supplier description mapping</button></form>}
    </>}
  </section>;
}
