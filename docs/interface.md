# Store workspace interface

The portal presents daily work through grouped navigation: Checkout, Stock & purchases, Money, and Management. Available modules follow the signed-in business capabilities. Store setup, terminals, staff permissions and audit history are under Settings & access. Development phases remain in project documentation rather than the customer interface.

Products, suppliers and stock counts use expandable entry sections. POS separates the current bill from payment, shows the server-calculated total prominently, and keeps the tax breakdown available on demand. Invoice intake explains Upload bill -> Extract & check -> Confirm purchase, while provider/model details remain under a disclosure. Processing updates the selected invoice and inbox status together. Extraction does not post stock or accounts.

The layout uses a sidebar on desktop and a horizontally scrollable navigation strip on smaller screens. Wide financial tables scroll within their own containers. Keyboard focus, a skip link, reduced-motion preferences, explicit financial confirmations, role restrictions, and offline receipt printing remain supported.

Validation: frontend unit/integration tests cover auth, catalog, Decimal-string requests, duplicate review, financial confirmations and retry behavior. Browser tests cover offline sales/recovery/refunds, private invoice upload replay, and desktop/tablet/mobile navigation plus real product entry and checkout quote. Browser data is explicitly synthetic and separate from original supplier bills. No paid extraction is required for these checks.

Existing business names are user data and are not renamed by the interface update. The current Gemini quota/billing setup is unchanged.
