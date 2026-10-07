# AI agents

Status: contracts/design only; no AI provider or fake responses in the pipeline baseline.

## Orchestration
BusinessAgent summarizes facts (Phase 3). InventoryAgent, PurchaseAgent, FinanceAgent, PricingAgent, ExpiryAgent and SupplierAgent arrive in Phase 4. GSTAgent arrives in Phase 5; StaffAgent/anomaly forecasting is Phase 6. Agents receive tenant/store-scoped typed tools, never arbitrary SQL or unrestricted database access.

Each agent has purpose, allowed tools, server-enforced permissions, input/output validation, audit/provenance, confidence/evidence quality and approval requirements. Figures are computed deterministically by tools and returned with interval, store, freshness and completeness. The model explains evidence; unavailable data is explicitly unavailable.

## OCR (Phase 2)
Upload -> quarantine/signature/size checks -> malware scan -> sandboxed preprocessing -> OCR -> classification -> supplier identification -> extraction -> product matching -> tax/arithmetic validation -> duplicate detection -> confidence -> human review -> atomic purchase/stock posting.

Adapters: MalwareScanner, DocumentPreprocessor, OCRProvider, InvoiceExtractor, SemanticMatcher and LanguageModel. An unconfigured adapter fails explicitly; OCR may not bypass an unavailable scanner. Provider credentials and processing region are deployment settings. Retain field value, confidence, raw evidence/page/bounds, extraction version, correction and reviewer.

Match barcode/SKU first, then historical supplier mapping, normalized name, brand, pack size and unit, fuzzy ranking, optional semantics. Candidate similarity is not calibrated confidence. Conflicting pack sizes/units block selection. Likely existing matches prevent automatic product creation. MVP OCR always needs review; critical fields below configured thresholds block posting.

## Actions and approval
Low-risk reports, categorization and detection may run automatically. Significant actions produce a proposal with exact affected records, old/new values, reason, versions, requester, evidence and expiry. Authorized approval binds to the proposal hash; execution revalidates state and permissions. Price changes, adjustments, refunds, payments, bulk changes and filing cannot silently execute. Posted transactions cannot be deleted.

## Proactive UX
Daily briefings separate sales, cash, COGS, expenses and estimated profit. Deduplicate recommendations, prioritize Critical/High/Medium/Informational and cap repeated notifications. Stockout forecasts include window, sample size, estimated range and confidence. Staff anomalies say "requires review".

## Evaluation gates
Invoice field accuracy, pack-size mismatch rejection, duplicate recall, arithmetic errors, low-confidence gating, grounded numeric answers, prompt-injection rejection, tool authorization and stale-approval rejection. Use anonymized approved evaluation data. Never invent business numbers in missing-data tests or production UI.
