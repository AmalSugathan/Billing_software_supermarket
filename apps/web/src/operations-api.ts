import { z } from 'zod';

const decimal = z.string().regex(/^\d+(\.\d+)?$/);
export const productSchema = z.object({
  id: z.string(), sku: z.string(), name: z.string(), unit: z.string(),
  category_id: z.string().nullable(), brand_id: z.string().nullable(), supplier_id: z.string().nullable(),
  purchase_price: decimal.nullable(), landed_cost: decimal.nullable(), selling_price: decimal, mrp: decimal,
  minimum_selling_price: decimal, target_margin: decimal.nullable(), gst_rate: decimal, hsn: z.string().nullable(),
  reorder_level: decimal, reorder_quantity: decimal, batch_tracking: z.boolean(), expiry_tracking: z.boolean(),
  active: z.boolean(), barcodes: z.array(z.string()), alternate_names: z.array(z.string()),
});
export const supplierSchema = z.object({ id: z.string(), name: z.string(), gstin: z.string().nullable(),
  phone: z.string(), address: z.string(), payment_terms_days: z.number().int(), active: z.boolean() });
export const namedSchema = z.object({ id: z.string(), name: z.string() });
export const candidateSchema = z.object({ id: z.string(), sku: z.string(), name: z.string(), similarity: z.number().int() });
export const stockSchema = z.object({ product_id: z.string(), sku: z.string(), name: z.string(), unit: z.string(),
  quantity: decimal, opening_value: decimal });
export const movementSchema = z.object({ id: z.string(), store_id: z.string(), product_id: z.string(), batch_id: z.string(),
  kind: z.string(), quantity: z.string().regex(/^-?\d+(\.\d+)?$/), unit_cost: decimal, reason: z.string(), actor_user_id: z.string(),
  source: z.string(), human_approved: z.boolean(), created_at: z.string() });
export type Product = z.infer<typeof productSchema>;
export type Supplier = z.infer<typeof supplierSchema>;
export type Named = z.infer<typeof namedSchema>;
export type Candidate = z.infer<typeof candidateSchema>;
export type Stock = z.infer<typeof stockSchema>;
export type Movement = z.infer<typeof movementSchema>;
export const operations = { product: productSchema, products: z.array(productSchema), supplier: supplierSchema,
  suppliers: z.array(supplierSchema), named: namedSchema, names: z.array(namedSchema), matches: z.array(candidateSchema),
  stock: z.array(stockSchema), movement: movementSchema, movements: z.array(movementSchema) };
