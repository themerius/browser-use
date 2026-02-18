"""Integration tests for outline mode with the full agent + mock LLM pipeline.

Validates that the agent can complete tasks when outline_mode=True and that
the outline format is correctly passed through the entire stack.
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

from browser_use import Agent
from browser_use.browser import BrowserProfile, BrowserSession
from tests.ci.conftest import create_mock_llm

# ─── HTML fixtures ────────────────────────────────────────────────────────────

WCAG_PAGE = """<!DOCTYPE html>
<html lang="en">
<head><title>Test Page</title></head>
<body>
	<header role="banner">
		<h1>Test Application</h1>
		<nav aria-label="Main">
			<a href="/">Home</a>
			<a href="/about">About</a>
		</nav>
	</header>
	<main role="main">
		<h2>Welcome</h2>
		<p>This is a test page with proper semantic HTML.</p>
		<form>
			<label for="search">Search:</label>
			<input type="text" id="search" name="q" placeholder="Search..." />
			<button type="submit">Go</button>
		</form>
	</main>
	<footer role="contentinfo">
		<p>&copy; 2026 Test</p>
	</footer>
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


async def test_agent_completes_with_outline_mode(httpserver: HTTPServer):
	"""Agent completes a basic task with outline_mode=True using mock LLM."""
	httpserver.expect_request('/').respond_with_data(WCAG_PAGE, content_type='text/html')
	base_url = httpserver.url_for('/')

	llm = create_mock_llm(actions=[
		"""{
			"thinking": "I see the page in outline mode. Let me complete the task.",
			"evaluation_previous_goal": "Starting task",
			"memory": "Page loaded",
			"next_goal": "Complete the task",
			"action": [
				{"done": {"text": "Task completed successfully", "success": true}}
			]
		}""",
	])

	session = BrowserSession(
		browser_profile=BrowserProfile(headless=True, user_data_dir=None, keep_alive=True)
	)
	await session.start()

	try:
		agent = Agent(
			task=f'Navigate to {base_url} and confirm the page loads.',
			llm=llm,
			browser_session=session,
			outline_mode=True,
		)

		history = await agent.run(max_steps=3)
		assert history.is_done()
		assert history.is_successful()
	finally:
		await session.kill()
		await session.event_bus.stop(clear=True, timeout=5)


async def test_outline_mode_false_no_regression(httpserver: HTTPServer):
	"""Agent completes a basic task with outline_mode=False (default) — no regression."""
	httpserver.expect_request('/').respond_with_data(WCAG_PAGE, content_type='text/html')
	base_url = httpserver.url_for('/')

	llm = create_mock_llm(actions=[
		"""{
			"thinking": "Page loaded. Completing task.",
			"evaluation_previous_goal": "Starting task",
			"memory": "Page loaded",
			"next_goal": "Complete the task",
			"action": [
				{"done": {"text": "Task completed successfully", "success": true}}
			]
		}""",
	])

	session = BrowserSession(
		browser_profile=BrowserProfile(headless=True, user_data_dir=None, keep_alive=True)
	)
	await session.start()

	try:
		agent = Agent(
			task=f'Navigate to {base_url} and confirm the page loads.',
			llm=llm,
			browser_session=session,
			outline_mode=False,
		)

		history = await agent.run(max_steps=3)
		assert history.is_done()
		assert history.is_successful()
	finally:
		await session.kill()
		await session.event_bus.stop(clear=True, timeout=5)


async def test_outline_mode_browser_state_hint(httpserver: HTTPServer):
	"""When outline_mode=True, browser state description includes format hint."""
	httpserver.expect_request('/').respond_with_data(WCAG_PAGE, content_type='text/html')
	base_url = httpserver.url_for('/')

	session = BrowserSession(
		browser_profile=BrowserProfile(headless=True, user_data_dir=None, keep_alive=True)
	)
	await session.start()

	try:
		await session.navigate_to(base_url)
		import asyncio
		await asyncio.sleep(1.0)

		state = await session.get_browser_state_summary()

		import tempfile

		from browser_use.agent.prompts import AgentMessagePrompt
		from browser_use.filesystem.file_system import FileSystem

		with tempfile.TemporaryDirectory() as tmpdir:
			fs = FileSystem(base_dir=tmpdir)

			# With outline_mode=True
			prompt_outline = AgentMessagePrompt(
				browser_state_summary=state,
				file_system=fs,
				outline_mode=True,
			)
			desc = prompt_outline._get_browser_state_description()
			assert 'Outline mode' in desc, f'Missing outline hint in browser state:\n{desc}'
			assert '(ungrouped)' in desc, f'Missing ungrouped explanation in browser state:\n{desc}'

			# With outline_mode=False — no hint
			prompt_classic = AgentMessagePrompt(
				browser_state_summary=state,
				file_system=fs,
				outline_mode=False,
			)
			desc_classic = prompt_classic._get_browser_state_description()
			assert 'Outline mode' not in desc_classic
	finally:
		await session.kill()
		await session.event_bus.stop(clear=True, timeout=5)


async def test_outline_mode_settings_propagation(httpserver: HTTPServer):
	"""Verify outline_mode setting is correctly propagated to AgentSettings."""
	httpserver.expect_request('/').respond_with_data(WCAG_PAGE, content_type='text/html')

	llm = create_mock_llm()

	session = BrowserSession(
		browser_profile=BrowserProfile(headless=True, user_data_dir=None, keep_alive=True)
	)
	await session.start()

	try:
		agent = Agent(
			task='Test outline mode propagation',
			llm=llm,
			browser_session=session,
			outline_mode=True,
		)

		# Verify the setting propagated to AgentSettings
		assert agent.settings.outline_mode is True

		agent2 = Agent(
			task='Test default mode',
			llm=llm,
			browser_session=session,
			outline_mode=False,
		)
		assert agent2.settings.outline_mode is False
	finally:
		await session.kill()
		await session.event_bus.stop(clear=True, timeout=5)
