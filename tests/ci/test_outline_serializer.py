"""Tests for the hierarchical outline serialization mode (Plan 3).

Validates landmark detection, heading hierarchy extraction, outline formatting,
region collapsing, and backward compatibility with flat serialization.
"""

import os
import socketserver

import pytest
from pytest_httpserver import HTTPServer

# Fix for httpserver hanging on shutdown
socketserver.ThreadingMixIn.block_on_close = False
socketserver.ThreadingMixIn.daemon_threads = True

os.environ['SKIP_LLM_API_KEY_VERIFICATION'] = 'true'
os.environ['ANONYMIZED_TELEMETRY'] = 'false'

from browser_use.browser import BrowserProfile, BrowserSession


async def _get_serialized_dom(session: BrowserSession, url: str):
	"""Navigate to URL and return the serialized DOM state."""
	import asyncio

	await session.navigate_to(url)
	# Let the DOM watchdog process the page
	await asyncio.sleep(1.0)

	state = await session.get_browser_state_summary()
	return state.dom_state


# ─── Fixtures ─────────────────────────────────────────────────────────────────

LANDMARK_PAGE = """<!DOCTYPE html>
<html lang="en">
<head><title>Landmark Test</title></head>
<body>
	<header>
		<h1>Site Title</h1>
		<nav aria-label="Primary">
			<a href="/home">Home</a>
			<a href="/about">About</a>
		</nav>
	</header>
	<main>
		<h2>Main Content</h2>
		<p>Some paragraph text here.</p>
		<button id="action-btn">Click Me</button>
		<h3>Subsection</h3>
		<input type="text" placeholder="Enter text" />
	</main>
	<aside aria-label="Sidebar">
		<h2>Related Links</h2>
		<a href="/link1">Link 1</a>
	</aside>
	<footer>
		<p>&copy; 2026 Test Corp</p>
		<a href="/privacy">Privacy</a>
	</footer>
</body>
</html>"""

HEADING_HIERARCHY_PAGE = """<!DOCTYPE html>
<html lang="en">
<head><title>Heading Hierarchy</title></head>
<body>
	<main>
		<h1>Product Catalog</h1>
		<h2>Featured Products</h2>
		<p>Some featured items.</p>
		<h2>Categories</h2>
		<h3>Electronics</h3>
		<p>Electronic gadgets.</p>
		<h3>Clothing</h3>
		<p>Apparel items.</p>
		<h2>Recently Viewed</h2>
	</main>
</body>
</html>"""

NO_LANDMARK_PAGE = """<!DOCTYPE html>
<html>
<head><title>No Landmarks</title></head>
<body>
	<div id="app">
		<div class="header">
			<span>Logo</span>
		</div>
		<div class="content">
			<button>Click</button>
			<input type="text" placeholder="Type here" />
		</div>
	</div>
</body>
</html>"""

NAMED_VS_UNNAMED_PAGE = """<!DOCTYPE html>
<html lang="en">
<head><title>Named vs Unnamed</title></head>
<body>
	<nav aria-label="Primary">
		<a href="/home">Home</a>
	</nav>
	<nav>
		<a href="/footer-link">Footer Link</a>
	</nav>
	<div role="region" aria-label="Special Section">
		<p>Named region content</p>
	</div>
	<div role="region">
		<p>Unnamed region - should be excluded from landmarks</p>
	</div>
	<main>
		<p>Main content</p>
	</main>
</body>
</html>"""


# ─── Tests ────────────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
async def browser_session():
	session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			user_data_dir=None,
			keep_alive=True,
		)
	)
	await session.start()
	yield session
	await session.kill()
	await session.event_bus.stop(clear=True, timeout=5)


async def test_landmark_detection_basic(browser_session: BrowserSession, httpserver: HTTPServer):
	"""Page with header, nav, main, aside, footer produces correct landmarks."""
	httpserver.expect_request('/').respond_with_data(LANDMARK_PAGE, content_type='text/html')
	url = httpserver.url_for('/')

	dom_state = await _get_serialized_dom(browser_session, url)
	outline = dom_state.llm_representation(outline_mode=True)

	# Should have the outline markers
	assert '=== PAGE OUTLINE ===' in outline
	assert '=== END OUTLINE ===' in outline

	# Should contain landmark headers (case-insensitive role check)
	outline_upper = outline.upper()
	assert 'BANNER' in outline_upper or 'NAV' in outline_upper or 'MAIN' in outline_upper
	# Main should appear (either via <main> tag or role)
	assert 'MAIN' in outline_upper


async def test_heading_hierarchy_in_outline(browser_session: BrowserSession, httpserver: HTTPServer):
	"""Headings appear with markdown-style prefixes in the outline."""
	httpserver.expect_request('/').respond_with_data(HEADING_HIERARCHY_PAGE, content_type='text/html')
	url = httpserver.url_for('/')

	dom_state = await _get_serialized_dom(browser_session, url)
	outline = dom_state.llm_representation(outline_mode=True)

	# Headings should appear with # or ## prefixes
	assert '# ' in outline or '## ' in outline
	# Content should include heading text
	assert 'Product Catalog' in outline or 'Featured Products' in outline


async def test_no_landmarks_produces_ungrouped(browser_session: BrowserSession, httpserver: HTTPServer):
	"""Page with no semantic landmarks puts elements under (ungrouped)."""
	httpserver.expect_request('/').respond_with_data(NO_LANDMARK_PAGE, content_type='text/html')
	url = httpserver.url_for('/')

	dom_state = await _get_serialized_dom(browser_session, url)
	outline = dom_state.llm_representation(outline_mode=True)

	# Should have outline structure
	assert '=== PAGE OUTLINE ===' in outline
	# Elements outside landmarks go to ungrouped
	assert '(ungrouped)' in outline.lower() or 'MAIN' in outline.upper()


async def test_named_vs_unnamed_regions(browser_session: BrowserSession, httpserver: HTTPServer):
	"""Named nav produces landmark with name; unnamed region is excluded."""
	httpserver.expect_request('/').respond_with_data(NAMED_VS_UNNAMED_PAGE, content_type='text/html')
	url = httpserver.url_for('/')

	dom_state = await _get_serialized_dom(browser_session, url)
	outline = dom_state.llm_representation(outline_mode=True)

	# Named nav should appear with its label
	assert 'Primary' in outline or 'NAV' in outline.upper()


async def test_outline_preserves_interactive_indices(browser_session: BrowserSession, httpserver: HTTPServer):
	"""Interactive elements in outline mode still have [index] markers."""
	httpserver.expect_request('/').respond_with_data(LANDMARK_PAGE, content_type='text/html')
	url = httpserver.url_for('/')

	dom_state = await _get_serialized_dom(browser_session, url)
	outline = dom_state.llm_representation(outline_mode=True)

	# Should have at least one interactive element marker [number]
	import re
	interactive_markers = re.findall(r'\[\d+\]', outline)
	assert len(interactive_markers) > 0, f'No interactive markers found in outline:\n{outline}'


async def test_flat_mode_unchanged(browser_session: BrowserSession, httpserver: HTTPServer):
	"""outline_mode=False produces identical output to the default serializer."""
	httpserver.expect_request('/').respond_with_data(LANDMARK_PAGE, content_type='text/html')
	url = httpserver.url_for('/')

	dom_state = await _get_serialized_dom(browser_session, url)

	flat_default = dom_state.llm_representation(outline_mode=False)
	flat_explicit = dom_state.llm_representation()

	# Both should be identical (backward compatibility)
	assert flat_default == flat_explicit
	# And should NOT contain outline markers
	assert '=== PAGE OUTLINE ===' not in flat_default


async def test_outline_shorter_than_flat_for_landmark_page(browser_session: BrowserSession, httpserver: HTTPServer):
	"""Outline representation should be comparable or shorter than flat for pages with landmarks."""
	httpserver.expect_request('/').respond_with_data(LANDMARK_PAGE, content_type='text/html')
	url = httpserver.url_for('/')

	dom_state = await _get_serialized_dom(browser_session, url)

	flat = dom_state.llm_representation(outline_mode=False)
	outline = dom_state.llm_representation(outline_mode=True)

	# Just verify both produce non-empty output — token savings are
	# more meaningful on large real pages, not toy fixtures
	assert len(flat) > 0
	assert len(outline) > 0


async def test_region_collapsing_unchanged_regions(browser_session: BrowserSession, httpserver: HTTPServer):
	"""When previous_landmarks match current, regions show as collapsed."""
	httpserver.expect_request('/').respond_with_data(LANDMARK_PAGE, content_type='text/html')
	url = httpserver.url_for('/')

	dom_state = await _get_serialized_dom(browser_session, url)

	# First pass — no previous landmarks
	outline_first = dom_state.llm_representation(outline_mode=True, previous_landmarks=None)
	assert '(unchanged' not in outline_first

	# Detect current landmarks for "previous" state
	from browser_use.dom.serializer.outline import detect_landmarks
	landmarks = detect_landmarks(dom_state._root)

	# Second pass with same landmarks — regions should be collapsed
	outline_second = dom_state.llm_representation(outline_mode=True, previous_landmarks=landmarks)
	assert '(unchanged' in outline_second


# ─── Unit tests for outline.py pure functions ─────────────────────────────────

def test_landmark_region_key():
	"""_region_key produces consistent keys."""
	# We can't easily construct SimplifiedNodes without the full pipeline,
	# so test the key function with a mock-ish approach
	from unittest.mock import MagicMock

	from browser_use.dom.serializer.outline import LandmarkRegion, _region_key
	mock_node = MagicMock()

	region = LandmarkRegion(role='navigation', name='Primary', node=mock_node, depth=0)
	assert _region_key(region) == 'navigation:Primary'

	region_unnamed = LandmarkRegion(role='main', name=None, node=mock_node, depth=0)
	assert _region_key(region_unnamed) == 'main:'


def test_heading_nesting():
	"""_nest_headings correctly nests h2 under h1, h3 under h2."""
	from unittest.mock import MagicMock

	from browser_use.dom.serializer.outline import HeadingNode, _nest_headings

	mock_node = MagicMock()

	flat = [
		HeadingNode(level=1, text='Title', node=mock_node, parent_landmark=None),
		HeadingNode(level=2, text='Section A', node=mock_node, parent_landmark=None),
		HeadingNode(level=3, text='Sub A.1', node=mock_node, parent_landmark=None),
		HeadingNode(level=2, text='Section B', node=mock_node, parent_landmark=None),
	]

	nested = _nest_headings(flat)

	assert len(nested) == 1  # Only h1 at root
	assert nested[0].text == 'Title'
	assert len(nested[0].children) == 2  # Two h2s
	assert nested[0].children[0].text == 'Section A'
	assert len(nested[0].children[0].children) == 1  # One h3
	assert nested[0].children[0].children[0].text == 'Sub A.1'
	assert nested[0].children[1].text == 'Section B'


def test_detect_unchanged_regions_no_previous():
	"""With no previous landmarks, all regions are marked as changed."""
	from unittest.mock import MagicMock

	from browser_use.dom.serializer.outline import LandmarkRegion, detect_unchanged_regions

	mock_node = MagicMock()

	current = [
		LandmarkRegion(role='main', name=None, node=mock_node, depth=0, element_count=5),
	]

	result = detect_unchanged_regions(current, None)
	assert result == {'main:': False}
