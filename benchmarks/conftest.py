"""Shared fixtures for the benchmark suite.

Key design note — **callable handlers for dynamic routes**:

  Static fixture dicts (path → HTML string) are the default for most benchmark
  tasks.  But for tasks where the *server response depends on user input* (e.g.
  validating a POST form body), fixtures can map a path to a callable instead.
  The callable receives a ``werkzeug.wrappers.Request`` and must return a
  ``werkzeug.wrappers.Response``.

  This was added after discovering that the Dropdown Interaction benchmark
  gave a false positive: the ``/confirm`` page was a static HTML page that
  always showed "Pro Plan" regardless of what the agent actually selected.
  Making it a handler that checks ``request.form['product']`` turned an
  undetectable regression into a caught failure.  See ``dropdown_interaction``
  in ``benchmarks/fixtures/__init__.py`` for the canonical example.
"""

import os
import socketserver
import tempfile
from urllib.parse import urlparse

from pytest_httpserver import HTTPServer

from browser_use.browser import BrowserProfile, BrowserSession

# Fix for httpserver hanging on shutdown
socketserver.ThreadingMixIn.block_on_close = False
socketserver.ThreadingMixIn.daemon_threads = True

# Skip LLM API key verification
os.environ['SKIP_LLM_API_KEY_VERIFICATION'] = 'true'
os.environ['ANONYMIZED_TELEMETRY'] = 'false'


def register_fixture_routes(server: HTTPServer, fixture_dict: dict[str, str | bytes | dict]) -> None:
	"""Register all routes from a fixture's path→content dict with an HTTPServer.

	Handles four content types:
	- str: HTML content served as text/html
	- bytes: Binary data served as application/octet-stream
	- dict: Rich response with 'data', 'content_type', and optional 'headers'
	- callable: Dynamic handler (werkzeug Request → Response)
	"""
	for path, content in fixture_dict.items():
		# Separate query string from path (pytest-httpserver requires them separate)
		parsed = urlparse(path)
		uri = parsed.path
		qs = parsed.query or None

		if callable(content):
			server.expect_request(uri, query_string=qs).respond_with_handler(content)
		elif isinstance(content, dict):
			server.expect_request(uri, query_string=qs).respond_with_data(
				content['data'],
				content_type=content.get('content_type', 'application/octet-stream'),
				headers=content.get('headers', {}),
			)
		elif isinstance(content, bytes):
			server.expect_request(uri, query_string=qs).respond_with_data(
				content,
				content_type='application/octet-stream',
			)
		else:
			server.expect_request(uri, query_string=qs).respond_with_data(
				content,
				content_type='text/html',
			)


async def create_benchmark_browser_session(downloads_path: str | None = None) -> BrowserSession:
	"""Create a browser session configured for benchmarking."""
	if downloads_path is None:
		downloads_path = tempfile.mkdtemp(prefix='benchmark_downloads_')

	session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			user_data_dir=None,
			keep_alive=True,
			downloads_path=downloads_path,
		)
	)
	await session.start()
	return session
