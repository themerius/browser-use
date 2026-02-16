"""HTML fixture generators for benchmark scenarios.

Each function returns a dict[str, str | bytes] mapping URL paths to response content.
Registered with pytest-httpserver for reproducible local testing.
"""


def _minimal_pdf(title: str = 'Test') -> bytes:
	"""Generate a minimal valid PDF binary for download testing."""
	# Minimal valid PDF structure
	content = f"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj

2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj

3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << >> >>
endobj

4 0 obj
<< /Length 44 >>
stream
BT /F1 12 Tf 100 700 Td ({title}) Tj ET
endstream
endobj

xref
0 5
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000230 00000 n

trailer
<< /Size 5 /Root 1 0 R >>
startxref
326
%%EOF"""
	return content.encode('latin-1')


def search_product() -> dict[str, str]:
	"""Search product scenario: index page with search, results page with product cards."""
	return {
		'/': """<!DOCTYPE html>
<html><head><title>Product Search</title></head>
<body>
	<h1>Product Store</h1>
	<form action="/search" method="get">
		<input type="text" name="q" placeholder="Search products..." />
		<button type="submit">Search</button>
	</form>
</body></html>""",
		'/search': """<!DOCTYPE html>
<html><head><title>Search Results</title></head>
<body>
	<h1>Search Results</h1>
	<div class="products">
		<div class="product-card">
			<h2>Wireless Headphones</h2>
			<p class="price">$79.99</p>
			<button class="add-to-cart">Add to Cart</button>
		</div>
		<div class="product-card">
			<h2>USB-C Hub</h2>
			<p class="price">$34.99</p>
			<button class="add-to-cart">Add to Cart</button>
		</div>
		<div class="product-card">
			<h2>Mechanical Keyboard</h2>
			<p class="price">$129.99</p>
			<button class="add-to-cart">Add to Cart</button>
		</div>
	</div>
</body></html>""",
	}


def fill_form() -> dict[str, str]:
	"""Multi-field contact form with confirmation page."""
	return {
		'/': """<!DOCTYPE html>
<html><head><title>Contact Form</title></head>
<body>
	<h1>Contact Us</h1>
	<form action="/submit" method="post">
		<label for="name">Name:</label>
		<input type="text" id="name" name="name" required />
		<label for="email">Email:</label>
		<input type="email" id="email" name="email" required />
		<label for="message">Message:</label>
		<textarea id="message" name="message" rows="4" required></textarea>
		<button type="submit">Submit</button>
	</form>
</body></html>""",
		'/submit': """<!DOCTYPE html>
<html><head><title>Form Submitted</title></head>
<body>
	<h1>Thank You!</h1>
	<p class="confirmation">Your message has been received. We will get back to you shortly.</p>
</body></html>""",
	}


def navigate_multi_page() -> dict[str, str]:
	"""Three-page sequential navigation with target info on final page."""
	return {
		'/': """<!DOCTYPE html>
<html><head><title>Page 1</title></head>
<body>
	<h1>Welcome - Page 1</h1>
	<p>This is the starting page.</p>
	<a href="/page2">Go to Page 2</a>
</body></html>""",
		'/page2': """<!DOCTYPE html>
<html><head><title>Page 2</title></head>
<body>
	<h1>Intermediate - Page 2</h1>
	<p>Keep going to find the information.</p>
	<a href="/page3">Go to Page 3</a>
</body></html>""",
		'/page3': """<!DOCTYPE html>
<html><head><title>Page 3</title></head>
<body>
	<h1>Final - Page 3</h1>
	<p class="target-info">The secret code is: BENCHMARK-42</p>
</body></html>""",
	}


def extract_structured_data() -> dict[str, str]:
	"""HTML table with 5 rows x 3 columns of product data."""
	return {
		'/': """<!DOCTYPE html>
<html><head><title>Product Inventory</title></head>
<body>
	<h1>Product Inventory</h1>
	<table class="inventory">
		<thead>
			<tr><th>Product</th><th>Price</th><th>Stock</th></tr>
		</thead>
		<tbody>
			<tr><td>Widget A</td><td>$12.50</td><td>142</td></tr>
			<tr><td>Widget B</td><td>$8.75</td><td>89</td></tr>
			<tr><td>Gadget X</td><td>$45.00</td><td>23</td></tr>
			<tr><td>Gadget Y</td><td>$67.25</td><td>7</td></tr>
			<tr><td>Doohickey Z</td><td>$3.99</td><td>500</td></tr>
		</tbody>
	</table>
</body></html>""",
	}


def dropdown_interaction() -> dict[str, str]:
	"""Form with native <select> dropdown, submit, and confirmation."""
	return {
		'/': """<!DOCTYPE html>
<html><head><title>Order Form</title></head>
<body>
	<h1>Place Your Order</h1>
	<form action="/confirm" method="post">
		<label for="product">Select Product:</label>
		<select id="product" name="product">
			<option value="">-- Choose --</option>
			<option value="basic">Basic Plan - $9.99/mo</option>
			<option value="pro">Pro Plan - $19.99/mo</option>
			<option value="enterprise">Enterprise Plan - $49.99/mo</option>
		</select>
		<button type="submit">Place Order</button>
	</form>
</body></html>""",
		'/confirm': """<!DOCTYPE html>
<html><head><title>Order Confirmed</title></head>
<body>
	<h1>Order Confirmed!</h1>
	<p class="confirmation">Your order for the Pro Plan has been placed successfully.</p>
</body></html>""",
	}


def download_newest_invoice() -> dict[str, str | bytes | dict]:
	"""Invoice portal with login page, invoice list, and PDF downloads.

	Returns a dict where:
	- String values are HTML content
	- bytes values are PDF binary data
	- dict values contain 'data', 'content_type', and 'headers' for special responses
	"""
	pdf_bytes = _minimal_pdf('INV-2026-005')

	return {
		'/portal': """<!DOCTYPE html>
<html><head><title>Invoice Portal</title></head>
<body>
	<h1>Company Portal</h1>
	<nav>
		<a href="/portal/invoices">Invoices</a>
		<a href="/portal/settings">Settings</a>
	</nav>
	<p>Welcome to the company portal. Select a section above.</p>
</body></html>""",
		'/portal/invoices': """<!DOCTYPE html>
<html><head><title>Invoices</title></head>
<body>
	<h1>Your Invoices</h1>
	<table class="invoices">
		<thead><tr><th>Invoice #</th><th>Date</th><th>Amount</th><th>Status</th><th></th></tr></thead>
		<tbody>
			<tr><td>INV-2026-005</td><td>2026-02-15</td><td>$1,249.00</td><td>Due</td>
				<td><a href="/portal/invoices/5/download" class="download-btn">Download PDF</a></td></tr>
			<tr><td>INV-2026-004</td><td>2026-01-20</td><td>$890.50</td><td>Paid</td>
				<td><a href="/portal/invoices/4/download" class="download-btn">Download PDF</a></td></tr>
			<tr><td>INV-2026-003</td><td>2025-12-15</td><td>$1,100.00</td><td>Paid</td>
				<td><a href="/portal/invoices/3/download" class="download-btn">Download PDF</a></td></tr>
			<tr><td>INV-2026-002</td><td>2025-11-18</td><td>$450.75</td><td>Paid</td>
				<td><a href="/portal/invoices/2/download" class="download-btn">Download PDF</a></td></tr>
			<tr><td>INV-2026-001</td><td>2025-10-10</td><td>$2,300.00</td><td>Paid</td>
				<td><a href="/portal/invoices/1/download" class="download-btn">Download PDF</a></td></tr>
		</tbody>
	</table>
</body></html>""",
		'/portal/invoices/5/download': {
			'data': pdf_bytes,
			'content_type': 'application/pdf',
			'headers': {'Content-Disposition': 'attachment; filename="INV-2026-005.pdf"'},
		},
		'/portal/invoices/4/download': {
			'data': _minimal_pdf('INV-2026-004'),
			'content_type': 'application/pdf',
			'headers': {'Content-Disposition': 'attachment; filename="INV-2026-004.pdf"'},
		},
		'/portal/invoices/3/download': {
			'data': _minimal_pdf('INV-2026-003'),
			'content_type': 'application/pdf',
			'headers': {'Content-Disposition': 'attachment; filename="INV-2026-003.pdf"'},
		},
		'/portal/invoices/2/download': {
			'data': _minimal_pdf('INV-2026-002'),
			'content_type': 'application/pdf',
			'headers': {'Content-Disposition': 'attachment; filename="INV-2026-002.pdf"'},
		},
		'/portal/invoices/1/download': {
			'data': _minimal_pdf('INV-2026-001'),
			'content_type': 'application/pdf',
			'headers': {'Content-Disposition': 'attachment; filename="INV-2026-001.pdf"'},
		},
	}


def _invoice_page_html(invoices: list[dict], page: int, total_pages: int) -> str:
	"""Generate a paginated invoice list page."""
	rows = ''
	for inv in invoices:
		rows += f"""
			<tr><td>{inv['number']}</td><td>{inv['date']}</td><td>{inv['amount']}</td><td>{inv['status']}</td>
				<td><a href="/portal/invoices/{inv['id']}/download" class="download-btn">Download PDF</a></td></tr>"""

	pagination = f'<span class="current">Page {page} of {total_pages}</span>'
	if page < total_pages:
		pagination += f'\n\t\t<a href="/portal/invoices?page={page + 1}" class="next-page">Next Page \u2192</a>'

	return f"""<!DOCTYPE html>
<html><head><title>Invoices - Page {page}</title></head>
<body>
	<h1>Your Invoices</h1>
	<table class="invoices">
		<thead><tr><th>Invoice #</th><th>Date</th><th>Amount</th><th>Status</th><th></th></tr></thead>
		<tbody>{rows}
		</tbody>
	</table>
	<div class="pagination">
		{pagination}
	</div>
</body></html>"""


def wcag_compliant_form() -> dict[str, str]:
	"""WCAG-compliant job application form with proper semantic HTML.

	Uses: <form>, <fieldset>, <legend>, <label for=>, <input>, <select>,
	<textarea>, <button type=submit>, aria-required, aria-describedby,
	landmark roles, proper heading hierarchy.
	"""
	return {
		'/': """<!DOCTYPE html>
<html lang="en">
<head>
	<meta charset="utf-8" />
	<title>Job Application - ACME Corp</title>
</head>
<body>
	<header role="banner">
		<h1>ACME Corp Careers</h1>
		<nav role="navigation" aria-label="Main navigation">
			<a href="/">Home</a> | <a href="/apply">Apply</a>
		</nav>
	</header>
	<main role="main">
		<h2>Job Application Form</h2>
		<p id="form-instructions">Please fill out all required fields marked with *.</p>
		<form action="/submit" method="post" aria-describedby="form-instructions">
			<fieldset>
				<legend>Personal Information</legend>
				<div>
					<label for="fullname">Full Name *</label>
					<input type="text" id="fullname" name="fullname" required aria-required="true" autocomplete="name" />
				</div>
				<div>
					<label for="email">Email Address *</label>
					<input type="email" id="email" name="email" required aria-required="true" autocomplete="email" />
				</div>
			</fieldset>
			<fieldset>
				<legend>Position Details</legend>
				<div>
					<label for="position">Position *</label>
					<select id="position" name="position" required aria-required="true">
						<option value="">-- Select a position --</option>
						<option value="junior">Junior Engineer</option>
						<option value="senior">Senior Engineer</option>
						<option value="staff">Staff Engineer</option>
						<option value="principal">Principal Engineer</option>
					</select>
				</div>
				<div>
					<label for="coverletter">Cover Letter *</label>
					<textarea id="coverletter" name="coverletter" rows="5" required aria-required="true"
						placeholder="Tell us why you're a great fit..."></textarea>
				</div>
			</fieldset>
			<button type="submit">Submit Application</button>
		</form>
	</main>
	<footer role="contentinfo">
		<p>&copy; 2026 ACME Corp</p>
	</footer>
</body>
</html>""",
		'/submit': """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8" /><title>Application Received</title></head>
<body>
	<main role="main">
		<h1>Application Received</h1>
		<p class="confirmation">Thank you, your application for <strong>Senior Engineer</strong> has been submitted successfully. We will contact you within 5 business days.</p>
	</main>
</body>
</html>""",
	}


def wcag_noncompliant_form() -> dict[str, str]:
	"""Non-WCAG-compliant job application form using pure div soup.

	Identical task and visual appearance, but NO semantic HTML:
	- No <form>, <input>, <select>, <textarea>, <button>, <label>, <fieldset>
	- Everything is <div> and <span> with CSS classes and contenteditable
	- Custom JS dropdown instead of <select>
	- Click-handler div instead of <button>
	- No ARIA attributes, no landmarks, no heading hierarchy
	"""
	return {
		'/': """<!DOCTYPE html>
<html>
<head>
	<title>Job Application - ACME Corp</title>
	<style>
		.page-title { font-size: 28px; font-weight: bold; margin: 20px 0; }
		.section-title { font-size: 20px; font-weight: bold; margin: 15px 0 10px; color: #333; }
		.nav-bar { margin-bottom: 20px; }
		.nav-bar .nav-item { color: blue; text-decoration: underline; cursor: pointer; margin-right: 10px; }
		.field-row { margin: 12px 0; }
		.field-label { font-size: 14px; color: #555; margin-bottom: 4px; }
		.text-field {
			border: 1px solid #ccc; padding: 8px; min-height: 20px;
			background: white; cursor: text; font-family: inherit; font-size: 14px;
		}
		.text-field:focus { outline: 2px solid #007bff; }
		.text-area-field {
			border: 1px solid #ccc; padding: 8px; min-height: 100px;
			background: white; cursor: text; font-family: inherit; font-size: 14px;
		}
		.custom-dropdown { position: relative; display: inline-block; width: 100%; }
		.dropdown-display {
			border: 1px solid #ccc; padding: 8px; background: white; cursor: pointer;
			font-size: 14px;
		}
		.dropdown-display::after { content: " ▼"; float: right; }
		.dropdown-options {
			display: none; position: absolute; top: 100%; left: 0; right: 0;
			border: 1px solid #ccc; background: white; z-index: 10; max-height: 200px;
			overflow-y: auto;
		}
		.dropdown-options.open { display: block; }
		.dropdown-option { padding: 8px; cursor: pointer; font-size: 14px; }
		.dropdown-option:hover { background: #e8e8e8; }
		.submit-btn {
			display: inline-block; padding: 10px 24px; background: #007bff; color: white;
			cursor: pointer; margin-top: 15px; font-size: 16px; border-radius: 4px;
		}
		.submit-btn:hover { background: #0056b3; }
		.group-box { border: 1px solid #ddd; padding: 15px; margin: 10px 0; }
		.group-title { font-weight: bold; margin-bottom: 10px; color: #333; }
		.footer-text { margin-top: 30px; color: #999; font-size: 12px; }
	</style>
</head>
<body>
	<div class="page-title">ACME Corp Careers</div>
	<div class="nav-bar">
		<span class="nav-item">Home</span>
		<span class="nav-item">Apply</span>
	</div>
	<div class="section-title">Job Application Form</div>
	<div class="field-hint">Please fill out all required fields marked with *.</div>

	<div class="group-box">
		<div class="group-title">Personal Information</div>
		<div class="field-row">
			<div class="field-label">Full Name *</div>
			<div class="text-field" contenteditable="true" data-field="fullname"></div>
		</div>
		<div class="field-row">
			<div class="field-label">Email Address *</div>
			<div class="text-field" contenteditable="true" data-field="email"></div>
		</div>
	</div>

	<div class="group-box">
		<div class="group-title">Position Details</div>
		<div class="field-row">
			<div class="field-label">Position *</div>
			<div class="custom-dropdown" id="position-dropdown">
				<div class="dropdown-display" id="position-display">-- Select a position --</div>
				<div class="dropdown-options" id="position-options">
					<div class="dropdown-option" data-value="junior">Junior Engineer</div>
					<div class="dropdown-option" data-value="senior">Senior Engineer</div>
					<div class="dropdown-option" data-value="staff">Staff Engineer</div>
					<div class="dropdown-option" data-value="principal">Principal Engineer</div>
				</div>
			</div>
		</div>
		<div class="field-row">
			<div class="field-label">Cover Letter *</div>
			<div class="text-area-field" contenteditable="true" data-field="coverletter"></div>
		</div>
	</div>

	<div class="submit-btn" id="submit-btn">Submit Application</div>

	<div class="footer-text">&copy; 2026 ACME Corp</div>

	<script>
		// Custom dropdown toggle
		document.getElementById('position-display').addEventListener('click', function() {
			document.getElementById('position-options').classList.toggle('open');
		});

		// Dropdown option selection
		document.querySelectorAll('.dropdown-option').forEach(function(opt) {
			opt.addEventListener('click', function() {
				document.getElementById('position-display').textContent = this.textContent;
				document.getElementById('position-display').dataset.value = this.dataset.value;
				document.getElementById('position-options').classList.remove('open');
			});
		});

		// Close dropdown on outside click
		document.addEventListener('click', function(e) {
			if (!document.getElementById('position-dropdown').contains(e.target)) {
				document.getElementById('position-options').classList.remove('open');
			}
		});

		// Submit handler
		document.getElementById('submit-btn').addEventListener('click', function() {
			window.location.href = '/submit';
		});
	</script>
</body>
</html>""",
		'/submit': """<!DOCTYPE html>
<html>
<head><title>Application Received</title></head>
<body>
	<div style="font-size: 24px; font-weight: bold; margin: 20px 0;">Application Received</div>
	<div class="confirmation">Thank you, your application for <span style="font-weight:bold">Senior Engineer</span> has been submitted successfully. We will contact you within 5 business days.</div>
</body>
</html>""",
	}


def download_all_invoices() -> dict[str, str | bytes | dict]:
	"""Paginated invoice portal with 8 invoices across 3 pages, each downloadable as PDF."""
	all_invoices = [
		{'id': 8, 'number': 'INV-2026-008', 'date': '2026-02-15', 'amount': '$1,249.00', 'status': 'Due'},
		{'id': 7, 'number': 'INV-2026-007', 'date': '2026-01-28', 'amount': '$730.00', 'status': 'Due'},
		{'id': 6, 'number': 'INV-2026-006', 'date': '2026-01-10', 'amount': '$890.50', 'status': 'Paid'},
		{'id': 5, 'number': 'INV-2026-005', 'date': '2025-12-20', 'amount': '$1,100.00', 'status': 'Paid'},
		{'id': 4, 'number': 'INV-2026-004', 'date': '2025-11-30', 'amount': '$450.75', 'status': 'Paid'},
		{'id': 3, 'number': 'INV-2026-003', 'date': '2025-11-01', 'amount': '$2,300.00', 'status': 'Paid'},
		{'id': 2, 'number': 'INV-2026-002', 'date': '2025-10-15', 'amount': '$675.25', 'status': 'Paid'},
		{'id': 1, 'number': 'INV-2026-001', 'date': '2025-09-20', 'amount': '$999.99', 'status': 'Paid'},
	]

	pages: dict[str, str | bytes | dict] = {
		'/portal/invoices?page=1': _invoice_page_html(all_invoices[0:3], page=1, total_pages=3),
		'/portal/invoices?page=2': _invoice_page_html(all_invoices[3:6], page=2, total_pages=3),
		'/portal/invoices?page=3': _invoice_page_html(all_invoices[6:8], page=3, total_pages=3),
	}

	# Add PDF download endpoints for all 8 invoices
	for inv in all_invoices:
		pages[f'/portal/invoices/{inv["id"]}/download'] = {
			'data': _minimal_pdf(inv['number']),
			'content_type': 'application/pdf',
			'headers': {'Content-Disposition': f'attachment; filename="{inv["number"]}.pdf"'},
		}

	return pages
