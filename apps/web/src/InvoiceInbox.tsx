import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { ApiError, request, type Business, type Session, type Store } from './identity-api';
import { loadPending, persistPending } from './commerce-api';
import { ocr, uploadInvoice, type InvoiceDocument, type OcrCandidate, type OcrProvider, type PurchaseSource, type InvoiceDraft } from './ocr-api';
import type { Supplier } from './operations-api';
const message = (error: unknown) => error instanceof Error ? error.message : 'The invoice action could not be completed.';

const extractionIssue = (code: string) => ({
  gemini_rate_limit: 'Gemini request limit reached. Retry later or check the API quota.',
  gemini_invalid_extraction: 'The extracted values did not pass validation. Retry extraction and check the original bill.',
  gemini_incomplete_or_blocked: 'Gemini could not read the complete invoice. Retry with a clearer source.',
  gemini_authentication: 'The Gemini API key needs checking on the server.',
  gemini_permission: 'This Gemini API key does not have permission to extract invoices.',
  gemini_model_unavailable: 'The configured Gemini model is unavailable.',
  gemini_service_unavailable: 'Gemini is temporarily unreachable. Your original bill is saved; retry later.',
  gemini_request_rejected: 'Gemini rejected the extraction request. The server configuration needs checking.',
}[code] ?? 'Extraction could not be completed. Your original bill is saved.');

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
          setDocuments((items) => items.map((item) => item.id === result.id ? result : item));
          if (result.status === 'processing') timer = setTimeout(poll, 3000);
          else { localStorage.removeItem(recovery + ':process:' + selectedId); setNotice('OCR status: ' + result.status.replaceAll('_', ' ') + '. No purchase was posted.'); }
        }
      }).catch((cause: unknown) => { if (!controller.signal.aborted) { setError(message(cause)); timer = setTimeout(poll, 10000); } });
    };
    timer = setTimeout(poll, 1000);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [route, selectedId, selectedStatus, recovery]);
  const structuredAttempt = selected?.evidence?.extraction ? selected.attempt_id : null;
  useEffect(() => {
    if (!selectedId || !structuredAttempt || selectedStatus !== 'review_required') return;
    const controller = new AbortController();
    void request(route + '/' + selectedId + '/draft', ocr.draft, { signal: controller.signal })
      .then((result) => { if (!controller.signal.aborted) setDraft(result); })
      .catch((cause: unknown) => { if (!controller.signal.aborted) setError(message(cause)); });
    return () => controller.abort();
  }, [route, selectedId, selectedStatus, structuredAttempt]);
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
      const command = loadPending(storageKey) ?? { route: route + '/' + selected.id + '/process', key: crypto.randomUUID(), body: { reason: 'User requested invoice extraction and pack review' } };
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
  return <section className="workspace-card invoice-workspace">
    <h4>Purchase invoice inbox</h4>
    <ol className="workflow-steps" aria-label="Invoice workflow"><li><span>1</span> Upload bill</li><li><span>2</span> Extract & check</li><li><span>3</span> Confirm purchase</li></ol><p className="muted">Check extracted quantities and pack sizes against the bill. Stock and supplier balances update only after purchase confirmation.</p>
    <label>Store<select value={storeId} disabled={busy} onChange={(event) => { setStoreId(event.target.value); setSelected(null); setDraft(null); setPreview(''); setCandidates([]); setProductId(''); setOffset(0); setError(''); }}>{stores.map((store) => <option key={store.id} value={store.id}>{store.name}</option>)}</select></label>
    {provider && <details className="service-details"><summary>Extraction service details</summary><p role="status">{provider.message} Model: {provider.provider_model}.</p></details>}
    {provider && !provider.upload_enabled && <p>Private document encryption must be configured before uploads are available.</p>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <form onSubmit={(event) => { void upload(event); }}>
      <label>Invoice image or PDF<input name="invoice" type="file" accept="image/jpeg,image/png,application/pdf" required disabled={busy || !provider?.upload_enabled} /></label>
      <label>Evidence origin<select name="origin" defaultValue="authentic"><option value="authentic">Original supplier bill</option><option value="synthetic">Synthetic practice bill</option><option value="unknown">Unknown origin</option></select></label>
      <p className="hint">Up to 10 MiB and 10 PDF pages. An uncertain upload must be retried with the same file and origin; no invoice file is kept in browser recovery storage.</p>
      <button disabled={busy || !provider?.upload_enabled}>Save invoice privately</button>
    </form>
    <button disabled={busy} onClick={() => { void action(async () => { const refreshed = await refresh(); setDocuments(refreshed.items); setProvider(refreshed.availability); }); }}>Refresh inbox</button>
    <ul className="invoice-list">{documents.map((document) => <li key={document.id}><button className="secondary-button" aria-pressed={selectedId === document.id} disabled={busy} onClick={() => { void action(async () => { if (selectedId !== document.id) setPreview(''); setCandidates([]); setDraft(null); setProductId(''); setSelected(await request(route + '/' + document.id, ocr.document)); }); }}>{document.filename} / {document.status.replaceAll('_', ' ')} / {document.data_origin}</button></li>)}</ul>
    <button disabled={busy || offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>Previous invoices</button>
    <button disabled={busy || documents.length < 50} onClick={() => setOffset(offset + 50)}>Next invoices</button>
    {selected && <>
      <h4>{selected.filename}</h4><p>Status: {selected.status.replaceAll('_', ' ')}. {selected.page_count} page(s). {selected.error_code && extractionIssue(selected.error_code)}</p>
      <a href={'/api/v1' + route + '/' + selected.id + '/content?original=true'} download>Download original bill</a>
      {preview && <details className="source-details" open><summary>Original bill</summary><img src={preview} alt="Supplier invoice source for human review" className="invoice-source-preview" /></details>}
      <button disabled={busy || !provider?.provider_configured || selected.status === 'processing'} onClick={() => { void process(); }}>Extract invoice</button>
      <p className="hint">Check the product, purchase unit and incoming stock before confirming a purchase. OCR confidence is unavailable; validation checks consistency, not reading accuracy.</p>
      <details><summary>Raw extraction evidence</summary>{selected.evidence?.pages.filter((page) => page.markdown || page.blocks.length).map((page) => <section key={page.page_number}><h5>OCR page {page.page_number}: unreviewed evidence</h5><pre className="invoice-ocr-evidence">{page.markdown || page.blocks.map((block) => block.text).join('\n')}</pre></section>)}</details>
      {selected.status === 'review_required' && !selected.evidence?.extraction && <button disabled={busy} onClick={() => { void action(async () => { setDraft(await request(route + '/' + selected.id + '/draft', ocr.draft)); }); }}>Prepare invoice fields for review</button>}
      {draft && draft.document_id === selected.id && <section>
        <h4>Invoice quantities and stock</h4>
        <ul>{draft.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
        <dl>{Object.entries(draft.fields).map(([name, field]) => <div key={name}><dt>{name.replaceAll('_', ' ')}</dt><dd>{field.value ?? 'Unavailable or ambiguous'}</dd></div>)}</dl>
        <p>Recognized line-total sum: {draft.line_total_sum ?? 'Unavailable'}. Compare every row with the original bill, including carton/piece conversions and free quantities.</p>
        <div className="table-scroll"><table aria-label="Extracted purchase quantities"><thead><tr><th>Product</th><th>Bill quantity</th><th>Purchase unit</th><th>Pack content</th><th>Incoming stock</th><th>Rate / purchase unit</th><th>Check</th></tr></thead><tbody>{draft.rows.map((row, index) => {
          const item = row.extraction;
          return <tr key={index}><td>{item?.normalized_name ?? row.fields.description?.value ?? 'Needs entry'}<details><summary>Source details</summary><p>{item?.original_name ?? row.fields.description?.source}</p><p>Page {row.page}. Printed unit: {item?.invoice_unit ?? row.fields.purchase_unit?.value ?? 'Unknown'}.</p>{item?.cost_per_kg && <p>INR {item.cost_per_kg}/kg at the printed rate, before line discount and tax adjustment.</p>}<pre className="invoice-ocr-evidence">{row.fields.description?.source}</pre></details></td><td>{item?.invoice_quantity ?? row.fields.quantity?.value ?? '?'}</td><td>{item?.purchase_unit_interpretation ?? row.fields.purchase_unit?.value ?? '?'}</td><td>{item ? `${item.detected_pack_size ?? '?'} ${item.pack_unit ?? ''}` : 'Confirm'}</td><td>{item ? `${item.stock_quantity ?? '?'} ${item.stock_unit ?? ''}` : 'Confirm conversion'}</td><td>{item?.unit_rate ?? row.fields.unit_rate?.value ?? '?'}</td><td>{!item || item.requires_review ? 'Needs review' : 'Arithmetic checked; confirm source'}</td></tr>;
        })}</tbody></table></div>
        {onPurchase && <button disabled={busy} onClick={() => onPurchase({ draft, storeId, filename: selected.filename })}>Review as purchase</button>}
        <p>These fields do not authorize stock or accounting changes. Approved purchase entry is still required, and supplier payment status is unknown.</p>
      </section>}
      <details><summary>Match a supplier description to an existing product</summary><h4>Find an existing product</h4><p className="hint">Name and pack-size matching searches up to 2,000 active products. Scores are similarity scores, not AI confidence. Verify the pack size and unit against the bill.</p>
      <label>Supplier description<input value={query} maxLength={250} onChange={(event) => { setQuery(event.target.value); setCandidates([]); setProductId(''); }} /></label>
      <label>Supplier<select value={supplierId} onChange={(event) => { setSupplierId(event.target.value); setCandidates([]); setProductId(''); }}><option value="">Select a supplier</option>{suppliers.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.name}</option>)}</select></label>
      <button disabled={busy || query.trim().length < 2} onClick={() => { void action(async () => { setCandidates(await request(route + '/' + selected.id + '/matches?q=' + encodeURIComponent(query) + (supplierId ? '&supplier_id=' + supplierId : ''), ocr.candidates)); }); }}>Find candidates</button>
      <ul>{candidates.map((candidate) => <li key={candidate.product_id}><label><input type="radio" name="match" checked={productId === candidate.product_id} onChange={() => setProductId(candidate.product_id)} />{candidate.name} / {candidate.sku} / {candidate.unit} / score {candidate.match_score} / {candidate.method.replaceAll('_', ' ')}</label></li>)}</ul>
      {canApprove && <form onSubmit={(event) => { void approveMapping(event); }}><label>Verification reason<input name="reason" minLength={3} maxLength={500} required /></label><label><input type="checkbox" required />I checked the product, pack size and unit against this invoice.</label><button disabled={busy || !supplierId || !productId}>Approve supplier description mapping</button></form>}
      </details>
    </>}
  </section>;
}
