import { z } from 'zod';

const decimal = z.string().regex(/^\d+(\.\d+)?$/);
export const purchaseLineSchema = z.object({ line_number: z.number().int(), product_id: z.string(), product_name: z.string(),
  sku: z.string(), stock_unit: z.string(), stock_quantity: decimal, taxable_value: decimal, cgst: decimal, sgst: decimal,
  igst: decimal, line_total: decimal, unit_cost: decimal });
export const previewSchema = z.object({ taxable_total: decimal, cgst: decimal, sgst: decimal, igst: decimal,
  round_off: z.string().regex(/^-?\d+(\.\d+)?$/), invoice_total: decimal, lines: z.array(purchaseLineSchema) });
export const purchaseSchema = previewSchema.extend({ id: z.string(), store_id: z.string(), supplier_id: z.string(),
  supplier_name: z.string(), supplier_gstin: z.string().nullable(), invoice_number: z.string(), invoice_date: z.string(),
  tax_mode: z.string(), tax_kind: z.string(), review_reason: z.string(), actor_user_id: z.string(), human_approved: z.boolean(),
  source: z.string(), created_at: z.string() });
export const purchaseSchemas = { preview: previewSchema, purchase: purchaseSchema, purchases: z.array(purchaseSchema) };
export type PurchasePreview = z.infer<typeof previewSchema>;
export type Purchase = z.infer<typeof purchaseSchema>;
