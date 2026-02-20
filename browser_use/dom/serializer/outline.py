# @file purpose: Landmark detection, heading hierarchy extraction, and region
# change detection for the outline serialization mode (Plan 3).
#
# Design decisions and known traps:
#
# 1. **No region collapsing**: An earlier iteration (v2) tried to detect
#    "unchanged" regions across steps and collapse them with a summary line.
#    This hid interactive elements from the LLM, causing WCAG form tasks to
#    spiral (13 steps, 161s).  Region collapsing was removed entirely in v3.
#
# 2. **Landmarkless pages**: When ``detect_landmarks`` returns an empty list,
#    the caller (``serialize_outline_tree``) must NOT fall back to classic
#    ``serialize_tree``.  Classic mode introduces ``|SHADOW(open)|`` prefixes
#    on native form elements (Chrome's internal shadow DOM) which misleads LLMs
#    into using ``click`` instead of ``select_dropdown``.  Instead, the caller
#    uses ``_serialize_outline_subtree`` directly on the root node — preserving
#    accessible-name annotations and clean formatting.
#    See: benchmarks/reports/outline_v3 (dropdown FAIL) vs v4 (dropdown PASS).
#
# 3. **Sub-region deduplication**: Elements inside nested landmarks (e.g. a
#    ``<nav>`` inside a ``<header>``) are emitted only under the most specific
#    landmark.  The parent landmark's subtree serializer receives an
#    ``exclude_ids`` set to skip the nested region's nodes.
#
# 4. **Named-only roles**: ``form`` and ``region`` landmarks are only included
#    when they have an accessible name (WAI-ARIA spec).  This avoids noisy
#    grouping under every ``<form>`` or ``<section>`` tag.

from __future__ import annotations

from dataclasses import dataclass, field

from browser_use.dom.views import NodeType, SimplifiedNode

# ── Landmark constants ────────────────────────────────────────────────────────

LANDMARK_ROLES: set[str] = {
	'banner',
	'navigation',
	'main',
	'complementary',
	'contentinfo',
	'search',
	'form',
	'region',
}

# HTML5 elements that imply landmark roles (WAI-ARIA spec).
# header/footer only count as banner/contentinfo when NOT nested inside
# <article> or <section>, but we rely on the AX tree for that nuance:
# if the AX role is set, the browser already applied the scoping rules.
IMPLICIT_LANDMARK_TAGS: dict[str, str] = {
	'header': 'banner',
	'nav': 'navigation',
	'main': 'main',
	'aside': 'complementary',
	'footer': 'contentinfo',
	'search': 'search',
}

# Roles that require an accessible name to qualify as landmarks (WAI-ARIA spec).
NAMED_ONLY_ROLES: set[str] = {'form', 'region'}


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class LandmarkRegion:
	"""A detected landmark region in the simplified DOM tree."""

	role: str  # e.g. "navigation", "main", "banner"
	name: str | None  # ax_node.name, e.g. "Primary navigation"
	node: SimplifiedNode  # The landmark root node
	depth: int  # Tree depth where this landmark lives
	children: list[SimplifiedNode] = field(default_factory=list)
	element_count: int = 0  # Total interactive elements in this region
	sub_regions: list[LandmarkRegion] = field(default_factory=list)


@dataclass
class HeadingNode:
	"""A heading element forming part of the document's heading hierarchy."""

	level: int  # 1–6
	text: str  # Heading text content
	node: SimplifiedNode  # The heading element
	parent_landmark: str | None  # Which landmark contains this heading
	children: list[HeadingNode] = field(default_factory=list)


# ── Landmark detection ────────────────────────────────────────────────────────

def _get_landmark_role(node: SimplifiedNode) -> str | None:
	"""Return the landmark role for *node*, or ``None`` if it is not a landmark.

	Checks the AX tree role first (most reliable — the browser already applied
	WAI-ARIA scoping rules), then falls back to implicit tag mapping.
	"""
	original = node.original_node

	# 1. Check AX role
	ax_role: str | None = None
	if original.ax_node and original.ax_node.role:
		ax_role = original.ax_node.role.lower()

	ax_name: str | None = None
	if original.ax_node and original.ax_node.name:
		ax_name = original.ax_node.name

	if ax_role and ax_role in LANDMARK_ROLES:
		# Named-only roles require an accessible name
		if ax_role in NAMED_ONLY_ROLES and not ax_name:
			return None
		return ax_role

	# 2. Fallback: implicit tag mapping
	if original.node_type == NodeType.ELEMENT_NODE:
		tag = original.tag_name.lower()
		implicit_role = IMPLICIT_LANDMARK_TAGS.get(tag)
		if implicit_role:
			# Named-only check for implicit roles too
			if implicit_role in NAMED_ONLY_ROLES and not ax_name:
				return None
			return implicit_role

	return None


def _get_ax_name(node: SimplifiedNode) -> str | None:
	"""Return the accessible name from the AX tree, if any."""
	if node.original_node.ax_node and node.original_node.ax_node.name:
		return node.original_node.ax_node.name
	return None


def _count_interactive(node: SimplifiedNode) -> int:
	"""Count interactive elements in *node* and all its descendants."""
	count = 1 if node.is_interactive else 0
	for child in node.children:
		count += _count_interactive(child)
	return count


def detect_landmarks(root: SimplifiedNode, depth: int = 0) -> list[LandmarkRegion]:
	"""Walk the ``SimplifiedNode`` tree and return a flat list of landmark regions.

	Nested landmarks (e.g. a ``<nav>`` inside ``<main>``) are recorded as
	``sub_regions`` of the outer landmark.  Top-level landmarks are returned
	directly.
	"""
	regions: list[LandmarkRegion] = []
	_detect_landmarks_recursive(root, depth, regions)
	return regions


def _detect_landmarks_recursive(
	node: SimplifiedNode,
	depth: int,
	accumulator: list[LandmarkRegion],
) -> None:
	role = _get_landmark_role(node)

	if role is not None:
		region = LandmarkRegion(
			role=role,
			name=_get_ax_name(node),
			node=node,
			depth=depth,
			children=list(node.children),
			element_count=_count_interactive(node),
		)
		# Recurse into children to find nested landmarks
		for child in node.children:
			_detect_landmarks_recursive(child, depth + 1, region.sub_regions)
		accumulator.append(region)
	else:
		# Not a landmark – keep looking in children
		for child in node.children:
			_detect_landmarks_recursive(child, depth + 1, accumulator)


# ── Heading hierarchy extraction ──────────────────────────────────────────────

HEADING_TAGS: set[str] = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}


def _get_heading_level(node: SimplifiedNode) -> int | None:
	"""Return the heading level (1–6) if *node* is a heading, else ``None``."""
	original = node.original_node

	# Check AX role first
	if original.ax_node and original.ax_node.role:
		if original.ax_node.role.lower() == 'heading':
			# Extract level from properties
			if original.ax_node.properties:
				for prop in original.ax_node.properties:
					if prop.name == 'level' and prop.value is not None:
						try:
							return int(prop.value)
						except (ValueError, TypeError):
							pass
			# Fallback: derive from tag name
			tag = original.tag_name.lower()
			if tag in HEADING_TAGS:
				return int(tag[1])
			return 1  # Default heading level

	# Fallback: tag-based detection
	if original.node_type == NodeType.ELEMENT_NODE:
		tag = original.tag_name.lower()
		if tag in HEADING_TAGS:
			return int(tag[1])

	return None


def _get_heading_text(node: SimplifiedNode) -> str:
	"""Return visible text content of a heading node."""
	original = node.original_node
	# Prefer AX name (visible text)
	if original.ax_node and original.ax_node.name:
		return original.ax_node.name
	return original.get_all_children_text(max_depth=3) or ''


def extract_heading_hierarchy(
	root: SimplifiedNode,
	parent_landmark: str | None = None,
) -> list[HeadingNode]:
	"""Walk the tree and build a hierarchical list of headings."""
	headings: list[HeadingNode] = []
	_collect_headings_flat(root, parent_landmark, headings)
	return _nest_headings(headings)


def _collect_headings_flat(
	node: SimplifiedNode,
	current_landmark: str | None,
	accumulator: list[HeadingNode],
) -> None:
	"""Collect all headings in DFS order into a flat list."""
	# Check if this node changes the current landmark context
	landmark_role = _get_landmark_role(node)
	if landmark_role is not None:
		landmark_label = landmark_role.upper()
		ax_name = _get_ax_name(node)
		if ax_name:
			landmark_label = f'{landmark_label}: "{ax_name}"'
		current_landmark = landmark_label

	level = _get_heading_level(node)
	if level is not None:
		accumulator.append(
			HeadingNode(
				level=level,
				text=_get_heading_text(node),
				node=node,
				parent_landmark=current_landmark,
			)
		)

	for child in node.children:
		_collect_headings_flat(child, current_landmark, accumulator)


def _nest_headings(flat: list[HeadingNode]) -> list[HeadingNode]:
	"""Convert a flat heading list into a nested hierarchy based on level."""
	if not flat:
		return []

	# Use a stack to build the hierarchy
	root: list[HeadingNode] = []
	stack: list[HeadingNode] = []

	for heading in flat:
		# Pop stack until we find a parent with a lower level
		while stack and stack[-1].level >= heading.level:
			stack.pop()

		if stack:
			stack[-1].children.append(heading)
		else:
			root.append(heading)

		stack.append(heading)

	return root


# ── Region change detection (cross-step collapsing) ──────────────────────────

def _region_key(region: LandmarkRegion) -> str:
	"""Create a stable key for a landmark region."""
	return f'{region.role}:{region.name or ""}'


def _region_element_ids(region: LandmarkRegion) -> frozenset[int]:
	"""Collect backend_node_ids of all interactive elements in a region."""
	ids: set[int] = set()
	_collect_backend_ids(region.node, ids)
	return frozenset(ids)


def _collect_backend_ids(node: SimplifiedNode, ids: set[int]) -> None:
	if node.is_interactive:
		ids.add(node.original_node.backend_node_id)
	for child in node.children:
		_collect_backend_ids(child, ids)


def detect_unchanged_regions(
	current: list[LandmarkRegion],
	previous: list[LandmarkRegion] | None,
) -> dict[str, bool]:
	"""Compare landmark regions between steps.

	Returns a dict mapping ``region_key`` → ``True`` if unchanged, ``False``
	otherwise.  If *previous* is ``None`` (first step), all regions are
	considered changed.
	"""
	if previous is None:
		return {_region_key(r): False for r in current}

	prev_map: dict[str, tuple[int, frozenset[int]]] = {}
	for r in previous:
		key = _region_key(r)
		prev_map[key] = (r.element_count, _region_element_ids(r))

	result: dict[str, bool] = {}
	for r in current:
		key = _region_key(r)
		if key not in prev_map:
			result[key] = False
			continue
		prev_count, prev_ids = prev_map[key]
		cur_ids = _region_element_ids(r)
		result[key] = (r.element_count == prev_count and cur_ids == prev_ids)

	return result
