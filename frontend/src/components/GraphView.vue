<script setup>
/**
 * Obsidian-style force graph over the document/project graph API (0.5 Slice 27).
 *
 * Plain 2D canvas + d3-force (the Obsidian approach): nodes are Source Sections
 * (colored by Section Type) plus one node per Source Document; PART_OF/HAS_SECTION
 * edges give the skeleton, REFERENCES edges the cross-links. Hover fades to the
 * neighborhood, click emits `select`, zoom/pan via d3-zoom, node drag re-heats.
 *
 * Canvas can't consume Tailwind classes, so semantic-token colors are resolved from
 * probe elements at mount and re-resolved on theme change.
 */
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useCall } from "frappe-ui";
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY } from "d3-force";
import { select } from "d3-selection";
import { zoom, zoomIdentity } from "d3-zoom";
import { drag } from "d3-drag";

const props = defineProps({
	url: { type: String, required: true }, // whitelisted graph method (api.graph.*)
	params: { type: Object, required: true },
});
const emit = defineEmits(["select"]);

const wrapper = ref(null);
const canvas = ref(null);
const tooltip = ref(null); // {x, y, lines} | null
const empty = ref(false);
const refCount = ref(0);

const graph = useCall({ url: props.url, method: "POST", immediate: false });
watch(
	() => props.params,
	(p) => graph.submit(p),
	{ immediate: true, deep: true }
);

// --- non-reactive render state (perf: the tick loop must not touch Vue proxies) ---
let ctx = null;
let nodes = [];
let links = [];
let neighbors = new Map(); // node id → Set of node ids (REFERENCES + hierarchy)
let sim = null;
let transform = zoomIdentity;
let hovered = null;
let hoveredLink = null;
let dragging = false;
let colors = {};
let typeColor = {};
let raf = 0;

// Deterministic ~12-hue palette: same Section Type → same color everywhere.
const HUES = [210, 25, 150, 45, 280, 0, 180, 320, 95, 250, 20, 130];
function hashHue(name) {
	let h = 0;
	for (const c of name) h = (h * 31 + c.charCodeAt(0)) | 0;
	return HUES[Math.abs(h) % HUES.length];
}

function probeColor(cls, prop = "color") {
	const el = document.createElement("div");
	el.className = cls;
	el.style.position = "absolute";
	el.style.visibility = "hidden";
	document.body.appendChild(el);
	const v = getComputedStyle(el)[prop];
	el.remove();
	return v;
}

function resolveColors() {
	colors = {
		hierarchy: probeColor("text-ink-gray-3"),
		reference: probeColor("text-ink-gray-6"),
		document: probeColor("text-ink-gray-8"),
		untyped: probeColor("text-ink-gray-4"),
		label: probeColor("text-ink-gray-7"),
	};
}

function radius(n) {
	if (n.kind === "document") return 11;
	return Math.min(14, 4 + Math.sqrt(n.span || 0) * 1.1);
}

function nodeColor(n) {
	if (n.kind === "document") return colors.document;
	if (!n.section_type) return colors.untyped;
	return typeColor[n.section_type] || colors.untyped;
}

// --- data → simulation -----------------------------------------------------------

function setGraph(data) {
	const ids = new Set(data.nodes.map((n) => n.id));
	nodes = data.nodes.map((n) => ({ ...n }));
	links = data.edges
		.filter((e) => ids.has(e.src) && ids.has(e.dst))
		.map((e) => ({ ...e, source: e.src, target: e.dst }));
	empty.value = nodes.filter((n) => n.kind === "section").length === 0;
	refCount.value = links.filter((l) => l.rel === "REFERENCES").length;

	typeColor = {};
	for (const t of data.meta?.types || []) typeColor[t.name] = `hsl(${hashHue(t.name)}, 60%, 52%)`;

	neighbors = new Map(nodes.map((n) => [n.id, new Set([n.id])]));
	for (const l of links) {
		neighbors.get(l.src)?.add(l.dst);
		neighbors.get(l.dst)?.add(l.src);
	}

	sim?.stop();
	sim = forceSimulation(nodes)
		.force(
			"link",
			forceLink(links)
				.id((d) => d.id)
				.distance((l) => (l.rel === "REFERENCES" ? 90 : 42))
				.strength((l) => (l.rel === "REFERENCES" ? Math.min(1, 0.3 + 0.1 * l.weight) : 0.5))
		)
		.force("charge", forceManyBody().strength(-160))
		.force("collide", forceCollide().radius((d) => radius(d) + 7))
		// Gentle centering (not forceCenter): keeps disconnected components on-canvas.
		.force("x", forceX(0).strength(0.06))
		.force("y", forceY(0).strength(0.06))
		.on("tick", scheduleDraw);
}

watch(
	() => graph.data,
	(data) => data && setGraph(data)
);

// --- rendering -------------------------------------------------------------------

function scheduleDraw() {
	if (!raf) raf = requestAnimationFrame(draw);
}

function activeSet() {
	if (hovered) return neighbors.get(hovered.id);
	return null;
}

function draw() {
	raf = 0;
	if (!ctx || !canvas.value) return;
	const dpr = window.devicePixelRatio || 1;
	const w = canvas.value.width / dpr;
	const h = canvas.value.height / dpr;
	ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
	ctx.clearRect(0, 0, w, h);
	ctx.translate(transform.x, transform.y);
	ctx.scale(transform.k, transform.k);

	const active = activeSet();
	const showAllLabels = transform.k > 1.1;

	for (const l of links) {
		const isRef = l.rel === "REFERENCES";
		const lit = !active || (active.has(l.source.id) && active.has(l.target.id));
		ctx.globalAlpha = lit ? (isRef ? 0.85 : 0.45) : 0.06;
		ctx.strokeStyle = isRef ? colors.reference : colors.hierarchy;
		ctx.lineWidth = l === hoveredLink ? 2.5 : isRef ? 1 + Math.log2(l.weight || 1) : 0.7;
		ctx.beginPath();
		ctx.moveTo(l.source.x, l.source.y);
		ctx.lineTo(l.target.x, l.target.y);
		ctx.stroke();
	}

	for (const n of nodes) {
		const lit = !active || active.has(n.id);
		ctx.globalAlpha = lit ? 1 : 0.12;
		ctx.fillStyle = nodeColor(n);
		ctx.beginPath();
		ctx.arc(n.x, n.y, radius(n), 0, 2 * Math.PI);
		ctx.fill();
		if (n.kind === "document") {
			ctx.strokeStyle = colors.document;
			ctx.lineWidth = 1.5;
			ctx.globalAlpha = lit ? 0.4 : 0.08;
			ctx.beginPath();
			ctx.arc(n.x, n.y, radius(n) + 3, 0, 2 * Math.PI);
			ctx.stroke();
		}
	}

	ctx.fillStyle = colors.label;
	ctx.textAlign = "center";
	ctx.font = `${10 / Math.max(1, transform.k * 0.8)}px sans-serif`;
	for (const n of nodes) {
		const lit = active ? active.has(n.id) : showAllLabels || n.kind === "document";
		if (!lit) continue;
		ctx.globalAlpha = active ? 1 : 0.75;
		const label = n.label.length > 34 ? n.label.slice(0, 32) + "…" : n.label;
		ctx.fillText(label, n.x, n.y - radius(n) - 4 / transform.k);
	}
	ctx.globalAlpha = 1;
}

// --- interaction -----------------------------------------------------------------

function findNode(px, py) {
	const [x, y] = transform.invert([px, py]);
	return sim?.find(x, y, 14 / transform.k);
}

function findRefLink(px, py) {
	const [x, y] = transform.invert([px, py]);
	const tol = 5 / transform.k;
	for (const l of links) {
		if (l.rel !== "REFERENCES") continue;
		const { x: x1, y: y1 } = l.source;
		const { x: x2, y: y2 } = l.target;
		const len2 = (x2 - x1) ** 2 + (y2 - y1) ** 2;
		if (!len2) continue;
		const t = Math.max(0, Math.min(1, ((x - x1) * (x2 - x1) + (y - y1) * (y2 - y1)) / len2));
		const dx = x - (x1 + t * (x2 - x1));
		const dy = y - (y1 + t * (y2 - y1));
		if (dx * dx + dy * dy < tol * tol) return l;
	}
	return null;
}

function onMove(e) {
	if (dragging) return;
	const rect = canvas.value.getBoundingClientRect();
	const px = e.clientX - rect.left;
	const py = e.clientY - rect.top;
	hovered = findNode(px, py) || null;
	hoveredLink = hovered ? null : findRefLink(px, py);
	canvas.value.style.cursor = hovered ? "pointer" : "default";
	if (hovered) {
		const lines = [hovered.label];
		if (hovered.kind === "section") {
			if (hovered.section_type) lines.push(hovered.section_type);
			if (hovered.page_start) lines.push(`pages ${hovered.page_start}–${hovered.page_end}`);
		} else {
			lines.push(`document · ${hovered.pages || "?"} pages`);
		}
		tooltip.value = { x: px + 12, y: py + 12, lines };
	} else if (hoveredLink) {
		tooltip.value = {
			x: px + 12,
			y: py + 12,
			lines: hoveredLink.anchors?.map((a) => `“${a}”`) || [],
		};
	} else {
		tooltip.value = null;
	}
	scheduleDraw();
}

function onLeave() {
	hovered = null;
	hoveredLink = null;
	tooltip.value = null;
	scheduleDraw();
}

function onClick(e) {
	if (e.defaultPrevented) return; // a drag gesture ended here
	const rect = canvas.value.getBoundingClientRect();
	const n = findNode(e.clientX - rect.left, e.clientY - rect.top);
	if (n) emit("select", n);
}

function setupInteraction() {
	const sel = select(canvas.value);
	sel.call(
		drag()
			.container(canvas.value)
			// Subject carries screen-space x/y so d3-drag's event.x/y stay in canvas
			// pixels regardless of zoom; the node itself rides along in world space.
			.subject((e) => {
				const n = findNode(e.x, e.y);
				return n ? { node: n, x: transform.applyX(n.x), y: transform.applyY(n.y) } : null;
			})
			.on("start", (e) => {
				dragging = true;
				if (!e.active) sim.alphaTarget(0.25).restart();
			})
			.on("drag", (e) => {
				e.subject.node.fx = transform.invertX(e.x);
				e.subject.node.fy = transform.invertY(e.y);
			})
			.on("end", (e) => {
				dragging = false;
				if (!e.active) sim.alphaTarget(0);
				e.subject.node.fx = null;
				e.subject.node.fy = null;
			})
	);
	sel.call(
		zoom()
			.scaleExtent([0.1, 8])
			// A press on a node belongs to drag; empty space (and wheel/pinch) to zoom.
			.filter((e) => {
				if (e.type === "mousedown" || e.type === "touchstart") {
					const rect = canvas.value.getBoundingClientRect();
					const t = e.touches?.[0] || e;
					return !findNode(t.clientX - rect.left, t.clientY - rect.top);
				}
				return !e.button;
			})
			.on("zoom", (e) => {
				transform = e.transform;
				scheduleDraw();
			})
	);
	// Start centered: world origin at the canvas middle.
	const { clientWidth: w, clientHeight: h } = wrapper.value;
	const initial = zoomIdentity.translate(w / 2, h / 2);
	sel.call(zoom().transform, initial);
	transform = initial;
}

function resize() {
	if (!canvas.value || !wrapper.value) return;
	const dpr = window.devicePixelRatio || 1;
	canvas.value.width = wrapper.value.clientWidth * dpr;
	canvas.value.height = wrapper.value.clientHeight * dpr;
	canvas.value.style.width = `${wrapper.value.clientWidth}px`;
	canvas.value.style.height = `${wrapper.value.clientHeight}px`;
	scheduleDraw();
}

let resizeObserver = null;
let themeObserver = null;

onMounted(() => {
	ctx = canvas.value.getContext("2d");
	resolveColors();
	resize();
	setupInteraction();
	canvas.value.addEventListener("mousemove", onMove);
	canvas.value.addEventListener("mouseleave", onLeave);
	canvas.value.addEventListener("click", onClick);
	resizeObserver = new ResizeObserver(resize);
	resizeObserver.observe(wrapper.value);
	// Dark/light switch flips an attribute on <html>; re-resolve token colors.
	themeObserver = new MutationObserver(() => {
		resolveColors();
		scheduleDraw();
	});
	themeObserver.observe(document.documentElement, {
		attributes: true,
		attributeFilter: ["data-theme", "class"],
	});
});

onBeforeUnmount(() => {
	sim?.stop();
	sim = null;
	if (raf) cancelAnimationFrame(raf);
	resizeObserver?.disconnect();
	themeObserver?.disconnect();
});
</script>

<template>
	<div ref="wrapper" class="relative h-full w-full overflow-hidden bg-surface-base">
		<canvas ref="canvas" class="block" />

		<div
			v-if="tooltip"
			class="pointer-events-none absolute z-10 max-w-64 rounded border border-outline-gray-2 bg-surface-white px-2 py-1 shadow-sm"
			:style="{ left: tooltip.x + 'px', top: tooltip.y + 'px' }"
		>
			<p
				v-for="(line, i) in tooltip.lines"
				:key="i"
				class="truncate text-xs"
				:class="i === 0 ? 'font-medium text-ink-gray-8' : 'text-ink-gray-5'"
			>
				{{ line }}
			</p>
		</div>

		<div v-if="graph.loading" class="absolute inset-0 grid place-items-center">
			<p class="text-sm text-ink-gray-5">Loading graph…</p>
		</div>
		<div v-else-if="empty" class="absolute inset-0 grid place-items-center">
			<p class="max-w-sm text-center text-sm text-ink-gray-5">
				No sections yet — parse the document and review its tree first.
			</p>
		</div>
		<p
			v-else-if="graph.data && !refCount"
			class="pointer-events-none absolute bottom-3 left-1/2 -translate-x-1/2 text-xs text-ink-gray-4"
		>
			Showing hierarchy only — reference links appear when sections cite pages.
		</p>
	</div>
</template>
