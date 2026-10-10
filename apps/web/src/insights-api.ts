import { z } from 'zod';
const money = z.string().regex(/^-?\d+\.\d{2}$/);
const count = z.number().int().nonnegative();
export const insightSchema = z.object({
  store_id: z.string(), start: z.string(), end: z.string(), timezone: z.string(), basis: z.string(), generated_at: z.string(),
  metrics: z.object({ revenue: money, billed: money, cogs: money, expenses: money, stock_loss: money, money_in: money, money_out: money, opening_funds: money, cash_variance: money, variance_absolute: money, sale_count: count, credit_count: count, variance_count: count, estimated_gross_profit: money, after_recorded_expenses: money, net_cash_flow: money }),
  snapshot: z.object({ recorded_balance: money, supplier_outstanding: money, inventory_value: money, low_stock_count: count, expiring_batch_count: count, expired_batch_count: count, stocked_product_count: count }),
  daily: z.array(z.object({ date: z.string(), revenue: money, money_in: money, money_out: money, net_cash_flow: money })),
  briefing: z.array(z.object({ priority: z.enum(['critical', 'high', 'medium', 'informational']), title: z.string(), detail: z.string(), module: z.string().nullable() })),
  briefing_method: z.literal('rules_based'), limitations: z.array(z.string()),
});
export type Insights = z.infer<typeof insightSchema>;
export function rupees(value: string): string {
  const [whole, fraction] = value.split('.');
  return (value.startsWith('-') ? '-' : '') + '\u20b9' + BigInt(whole.replace('-', '')).toLocaleString('en-IN') + '.' + fraction;
}
