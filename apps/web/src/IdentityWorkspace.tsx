import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { ApiError, request, schemas, type Audit, type Business, type Member, type Session,
  type Store, type Terminal } from './identity-api';

function message(error: unknown) { return error instanceof Error ? error.message : 'Something went wrong. Please retry.'; }
function field(form: FormData, name: string) { return String(form.get(name) ?? ''); }
type WorkspaceData = { stores: Store[]; terminals: Terminal[]; members: Member[]; audit: Audit[]; businessId: string };

function AuthForm({ onSuccess }: { onSuccess: (session: Session) => void }) {
  const [register, setRegister] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(''); setPending(true);
    const form = new FormData(event.currentTarget);
    try {
      const payload = { email: field(form, 'email'), password: field(form, 'password'),
        ...(register ? { display_name: field(form, 'display_name') } : {}) };
      onSuccess(await request(register ? '/auth/register' : '/auth/login', schemas.session, { method: 'POST', body: payload }));
    } catch (error) { setError(message(error)); }
    finally { setPending(false); }
  }
  return <section className="workspace-card auth-card">
    <p className="eyebrow">YOUR STORE WORKSPACE</p><h2>{register ? 'Create your account' : 'Welcome back'}</h2>
    <p className="muted">{register ? 'Set up your account, then add your business and first store.' : 'Sign in to manage your supermarket.'}</p>
    <form onSubmit={(event) => void submit(event)}>
      {register && <label>Your name<input name="display_name" autoComplete="name" required maxLength={100} /></label>}
      <label>Email<input name="email" type="email" autoComplete="username" required maxLength={254} /></label>
      <label>Password<input name="password" type="password" autoComplete={register ? 'new-password' : 'current-password'} required minLength={12} maxLength={128} /></label>
      <p className="hint">Use at least 12 characters.</p>
      {error && <p className="form-error" role="alert">{error}</p>}
      <button disabled={pending}>{pending ? 'Please wait…' : register ? 'Create account' : 'Sign in'}</button>
    </form>
    <button type="button" className="text-button" disabled={pending} onClick={() => { setRegister(!register); setError(''); }}>
      {register ? 'Already have an account? Sign in' : 'New here? Create an account'}
    </button>
  </section>;
}

export default function IdentityWorkspace() {
  const [session, setSession] = useState<Session | null>(null);
  const [initializing, setInitializing] = useState(true);
  const [businesses, setBusinesses] = useState<Business[]>([]);
  const [selected, setSelected] = useState('');
  const [stores, setStores] = useState<Store[]>([]);
  const [terminals, setTerminals] = useState<Terminal[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [audit, setAudit] = useState<Audit[]>([]);
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const [addingBusiness, setAddingBusiness] = useState(false);
  const [loadedBusiness, setLoadedBusiness] = useState('');
  const business = businesses.find((item) => item.id === selected) ?? businesses[0];
  const businessId = business?.id ?? '';
  const can = useCallback((capability: string) => business?.capabilities.includes(capability) ?? false, [business]);

  useEffect(() => {
    const controller = new AbortController();
    void request('/auth/session', schemas.session, { signal: controller.signal })
      .then(setSession).catch((error: unknown) => {
        if (!controller.signal.aborted && !(error instanceof ApiError && error.status === 401)) setError(message(error));
      }).finally(() => { if (!controller.signal.aborted) setInitializing(false); });
    return () => controller.abort();
  }, []);

  const loadBusinesses = useCallback(async () => {
    const result = await request('/businesses', schemas.businesses);
    setBusinesses(result); return result;
  }, []);
  useEffect(() => {
    if (!session) return;
    let active = true;
    void request('/businesses', schemas.businesses).then((result) => { if (active) setBusinesses(result); })
      .catch((error: unknown) => { if (active) setError(message(error)); });
    return () => { active = false; };
  }, [session]);

  const loadWorkspace = useCallback(async (signal?: AbortSignal) => {
    if (!businessId) return null;
    const base = '/businesses/' + businessId;
    const [newStores, newTerminals, newMembers, newAudit] = await Promise.all([
      request(base + '/stores', schemas.stores, { signal }),
      request(base + '/terminals', schemas.terminals, { signal }),
      can('staff.manage') ? request(base + '/members', schemas.members, { signal }) : Promise.resolve([]),
      can('audit.read') && business?.all_stores ? request(base + '/audit', schemas.audit, { signal }) : Promise.resolve([]),
    ]);
    return { stores: newStores, terminals: newTerminals, members: newMembers, audit: newAudit, businessId };
  }, [businessId, can, business?.all_stores]);
  const applyWorkspace = useCallback((data: WorkspaceData | null) => {
    if (!data) return;
    setStores(data.stores); setTerminals(data.terminals); setMembers(data.members); setAudit(data.audit); setLoadedBusiness(data.businessId);
  }, []);
  useEffect(() => {
    if (!businessId) return;
    const controller = new AbortController();
    void loadWorkspace(controller.signal).then((data) => { if (!controller.signal.aborted) applyWorkspace(data); })
      .catch((error: unknown) => { if (!controller.signal.aborted) setError(message(error)); });
    return () => controller.abort();
  }, [businessId, loadWorkspace, applyWorkspace]);

  async function action(work: () => Promise<void>) {
    setPending(true); setError('');
    try { await work(); return true; }
    catch (error) {
      if (error instanceof ApiError && error.status === 401) { setSession(null); setBusinesses([]); }
      setError(message(error)); return false;
    } finally { setPending(false); }
  }

  if (initializing) return <p role="status">Opening your workspace…</p>;
  if (!session) return <><AuthForm onSuccess={(value) => { setSession(value); setError(''); }} />{error && <p role="alert" className="form-error">{error}</p>}</>;

  async function setup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    await action(async () => {
      const created = await request('/businesses', schemas.business, { method: 'POST', csrf: session?.csrf_token,
        body: { name: field(form, 'business_name'), store_name: field(form, 'store_name'), store_address: field(form, 'address') } });
      await loadBusinesses(); setSelected(created.id); setAddingBusiness(false);
    });
  }

  async function createStore(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const element = event.currentTarget; const form = new FormData(element);
    if (await action(async () => {
      await request('/businesses/' + businessId + '/stores', schemas.store, { method: 'POST', csrf: session?.csrf_token,
        body: { name: field(form, 'name'), address: field(form, 'address') } });
      applyWorkspace(await loadWorkspace());
    })) element.reset();
  }

  async function createTerminal(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const element = event.currentTarget; const form = new FormData(element);
    if (await action(async () => {
      await request('/businesses/' + businessId + '/stores/' + field(form, 'store_id') + '/terminals', schemas.terminal,
        { method: 'POST', csrf: session?.csrf_token, body: { name: field(form, 'name') } });
      applyWorkspace(await loadWorkspace());
    })) element.reset();
  }

  async function addMember(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const element = event.currentTarget; const form = new FormData(element);
    const role = field(form, 'role'); const storeId = field(form, 'store_id');
    if (await action(async () => {
      await request('/businesses/' + businessId + '/members', schemas.member, { method: 'POST', csrf: session?.csrf_token,
        body: { email: field(form, 'email'), role, store_ids: ['OWNER', 'ADMIN', 'ACCOUNTANT'].includes(role) ? [] : [storeId] } });
      applyWorkspace(await loadWorkspace());
    })) element.reset();
  }

  const current = loadedBusiness === businessId;
  const roleChoices = business?.role === 'OWNER' ? ['CASHIER', 'INVENTORY_MANAGER', 'PURCHASE_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'ADMIN', 'OWNER'] : ['ADMIN'];
  return <section className="workspace" aria-labelledby="workspace-heading">
    <div className="workspace-header"><div><p className="eyebrow">YOUR WORKSPACE</p><h2 id="workspace-heading">Hello, {session.user.display_name}</h2></div>
      <button className="secondary-button" disabled={pending} onClick={() => void action(async () => {
        await request('/auth/logout', schemas.empty, { method: 'POST', csrf: session.csrf_token });
        setSession(null); setBusinesses([]); setStores([]); setTerminals([]); setMembers([]); setAudit([]); setSelected(''); setLoadedBusiness('');
      })}>Sign out</button>
    </div>
    {error && <p className="form-error" role="alert">{error}</p>}
    {!business || addingBusiness ? <div className="workspace-card">
      <h3>Set up your supermarket</h3><p className="muted">Start with your business and first store. Currency: INR · Timezone: Asia/Kolkata.</p>
      <form onSubmit={(event) => void setup(event)} className="form-grid">
        <label>Business name<input name="business_name" required maxLength={150} /></label>
        <label>First store name<input name="store_name" required maxLength={150} /></label>
        <label className="full-width">Store address<textarea name="address" maxLength={500} /></label>
        <button disabled={pending}>{pending ? 'Saving…' : 'Create business and store'}</button>
        {business && <button type="button" className="secondary-button" onClick={() => setAddingBusiness(false)}>Cancel</button>}
      </form>
    </div> : <>
      <div className="business-switch"><label>Business<select value={businessId} disabled={pending} onChange={(event) => { setSelected(event.target.value); setError(''); }}>
        {businesses.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select></label><span className="role-badge">{business.role.replaceAll('_', ' ')}</span>
      <button className="text-button" disabled={pending} onClick={() => setAddingBusiness(true)}>Add business</button></div>
      {!current ? <p role="status">Loading your store access…</p> : <div className="workspace-grid">
        <section className="workspace-card"><h3>Stores</h3>{stores.length ? <ul className="record-list">{stores.map((store) => <li key={store.id}><strong>{store.name}</strong><span>{store.address || 'No address recorded'}</span></li>)}</ul> : <p>No stores assigned to this account.</p>}
          {can('stores.manage') && business.all_stores && <form onSubmit={(event) => void createStore(event)}>
            <label>New store name<input name="name" required maxLength={150} /></label><label>Address<input name="address" maxLength={500} /></label>
            <button disabled={pending}>Add store</button></form>}
        </section>
        <section className="workspace-card"><h3>Billing terminals</h3><p className="hint">Terminal setup is available. Checkout comes in a later Phase 1 increment.</p>
          {terminals.length ? <ul className="record-list">{terminals.map((terminal) => <li key={terminal.id}><strong>{terminal.name}</strong><span>{stores.find((store) => store.id === terminal.store_id)?.name}</span></li>)}</ul> : <p>No terminals configured.</p>}
          {can('stores.manage') && stores.length > 0 && <form onSubmit={(event) => void createTerminal(event)}>
            <label>Store<select name="store_id">{stores.map((store) => <option key={store.id} value={store.id}>{store.name}</option>)}</select></label>
            <label>Terminal name<input name="name" required maxLength={100} /></label><button disabled={pending}>Add terminal</button>
          </form>}
        </section>
        {can('staff.manage') && <section className="workspace-card full-width"><h3>Staff access</h3><p className="hint">Staff must create their own account before you grant access. No invitation email is sent.</p>
          <ul className="record-list">{members.map((member) => <li key={member.id}><strong>{member.display_name}</strong><span>{member.email} · {member.role.replaceAll('_', ' ')} · {member.all_stores ? 'All stores' : member.store_ids.map((id) => stores.find((store) => store.id === id)?.name).join(', ')}</span></li>)}</ul>
          <form className="form-grid" onSubmit={(event) => void addMember(event)}>
            <label>Staff email<input name="email" type="email" required maxLength={254} /></label>
            <label>Role<select name="role">{roleChoices.map((role) => <option key={role} value={role}>{role.replaceAll('_', ' ')}</option>)}</select></label>
            <label>Store (for store-scoped roles)<select name="store_id">{stores.map((store) => <option key={store.id} value={store.id}>{store.name}</option>)}</select></label>
            <button disabled={pending}>Grant staff access</button>
          </form>
        </section>}
        {can('audit.read') && business.all_stores && <section className="workspace-card full-width"><h3>Recent audit events</h3>
          <ul className="record-list">{audit.map((entry) => <li key={entry.id}><strong>{entry.action.replaceAll('.', ' · ')}</strong><span>{new Date(entry.created_at).toLocaleString()} · {entry.source} · actor {entry.actor_user_id}</span></li>)}</ul>
          {!audit.length && <p>No events recorded.</p>}
        </section>}
      </div>}
    </>}
  </section>;
}
