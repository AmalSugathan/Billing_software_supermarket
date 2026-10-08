import { z } from 'zod';
const decimal = z.string().regex(/^\d+(\.\d+)?$/);
const line = z.object({ line_number: z.number().int(), product_id: z.string(), product_name: z.string(), sku: z.string(), unit: z.string(), hsn: z.string().nullable(), quantity: decimal, unit_price: decimal, gross: decimal, discount: decimal, gst_rate: decimal, taxable: decimal, cgst: decimal, sgst: decimal, total: decimal });
const totals = z.object({ gross_total: decimal, discount: decimal, taxable_total: decimal, cgst: decimal, sgst: decimal, total: decimal, lines: z.array(line) });
const receipt = totals.extend({ id: z.string(), store_id: z.string(), terminal_id: z.string(), invoice_number: z.string(), actor_user_id: z.string(), created_at: z.string(), payments: z.array(z.object({ id: z.string(), account_id: z.string(), method: z.string(), amount: decimal, movement_id: z.string() })) });
const payable = z.object({ purchase_id: z.string(), supplier_id: z.string(), supplier_name: z.string(), invoice_number: z.string(), total: decimal, paid: decimal, outstanding: decimal });
const supplierPayment = z.object({ id: z.string(), purchase_id: z.string(), supplier_id: z.string(), account_id: z.string(), movement_id: z.string(), amount: decimal, reference: z.string(), reason: z.string(), actor_user_id: z.string(), created_at: z.string() });
export const commerce = { quote: totals, receipt, receipts: z.array(receipt), payables: z.array(payable), supplierPayment, supplierPayments: z.array(supplierPayment) };
export type Quote = z.infer<typeof totals>;
export type Receipt = z.infer<typeof receipt>;
export type Payable = z.infer<typeof payable>;
export type SupplierPayment = z.infer<typeof supplierPayment>;
export const pendingCommand = z.object({ route: z.string(), key: z.string().uuid(), body: z.record(z.string(), z.unknown()) });
export type PendingCommand = z.infer<typeof pendingCommand>;
export function loadPending(storageKey: string): PendingCommand | null {
  const saved = localStorage.getItem(storageKey);
  return saved === null ? null : pendingCommand.parse(JSON.parse(saved));
}
export function persistPending(storageKey: string, command: PendingCommand) {
  localStorage.setItem(storageKey, JSON.stringify(command));
  if (localStorage.getItem(storageKey) !== JSON.stringify(command)) throw new Error('Checkout recovery storage is unavailable.');
}
export function addOne(quantity: string): string {
  if (!/^\d{1,12}(\.\d{1,3})?$/.test(quantity)) throw new Error('Enter a valid quantity first.');
  const [whole, fraction = ''] = quantity.split('.');
  const units = BigInt(whole) * 1000n + BigInt(fraction.padEnd(3, '0')) + 1000n;
  return (units / 1000n).toString() + '.' + (units % 1000n).toString().padStart(3, '0');
}
