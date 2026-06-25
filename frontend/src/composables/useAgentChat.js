// Chat controller for the AI agent panel (à la Builder's AIChatController, simplified):
// reactive messages + streaming accumulators; submitPrompt/cancel/loadSession. Calls the
// whitelisted `wikify.api.agent.*` methods, optimistically pushes the user bubble + a
// "Thinking…" assistant bubble, then lets realtime drive.
import { ref, watch } from "vue";
import { call } from "frappe-ui";
import { bindAgentRealtime } from "@/agent/realtime";
import { defaultAttachments } from "@/data/agentContext";

const THINKING_ID = "__thinking__";

function scopeOf(attachments) {
	if (attachments.some((a) => a.type === "section")) return "section";
	if (attachments.some((a) => a.type === "page")) return "page";
	if (attachments.some((a) => a.type === "document")) return "document";
	if (attachments.some((a) => a.type === "project")) return "project";
	return "global";
}

export function useAgentChat() {
	const messages = ref([]);
	const prompt = ref("");
	const sessionId = ref(null);
	const isRunning = ref(false);
	const errorText = ref("");
	const sessions = ref([]);
	const model = ref("");
	const models = ref([]);
	// Editable copy of the default context chips — re-seeded from the store when the
	// surface (project/document/page/section) changes; the user can remove a chip with ✕.
	const attachments = ref([]);
	// The last turn we submitted, so an errored turn can be retried verbatim.
	let lastSubmit = null;
	let unbind = null;

	watch(
		defaultAttachments,
		(next) => {
			attachments.value = (next || []).map((a) => ({ ...a }));
		},
		{ immediate: true, deep: true }
	);

	function removeAttachment(att) {
		attachments.value = attachments.value.filter(
			(a) => !(a.type === att.type && a.name === att.name)
		);
	}

	function rebind() {
		unbind?.();
		unbind = bindAgentRealtime(sessionId.value, {
			onStream: ({ message_id, chunk }) => {
				const m = adoptStreaming(message_id);
				m.content += chunk;
			},
			onTool: ({ name, args, status, summary, call_id }) => {
				let card = messages.value.find((x) => x.role === "tool" && x.callId === call_id);
				if (!card) {
					card = {
						id: `tool-${call_id}`,
						role: "tool",
						toolName: name,
						args,
						status,
						content: summary || "",
						callId: call_id,
					};
					// Insert the tool card before any trailing "Thinking…" bubble.
					const at = messages.value.findIndex((x) => x.id === THINKING_ID);
					if (at >= 0) messages.value.splice(at, 0, card);
					else messages.value.push(card);
				} else {
					card.status = status;
					if (summary) card.content = summary;
				}
			},
			onConfirm: ({ name, args, call_id, summary }) => {
				// Expensive/destructive tool held for approval — render a confirm card.
				dropThinking();
				messages.value.push({
					id: `confirm-${call_id}`,
					role: "confirm",
					toolName: name,
					args,
					summary,
					status: "pending",
				});
			},
			onClarify: ({ message_id, question, options }) => {
				isRunning.value = false;
				dropThinking();
				messages.value.push({
					id: message_id,
					role: "clarify",
					status: "clarification",
					content: question,
					options: options || [],
				});
			},
			onComplete: ({ message_id }) => {
				isRunning.value = false;
				dropThinking();
				const m = messages.value.find((x) => x.id === message_id);
				if (m) m.status = "done";
			},
			onError: ({ message }) => {
				isRunning.value = false;
				dropThinking();
				errorText.value = message;
				messages.value.push({
					id: `err-${Date.now()}`,
					role: "assistant",
					status: "error",
					content: message,
				});
			},
		});
	}

	// Turn the optimistic "Thinking…" bubble into the real streaming bubble on first chunk.
	function adoptStreaming(messageId) {
		let m = messages.value.find((x) => x.id === messageId);
		if (m) return m;
		const placeholder = messages.value.find((x) => x.id === THINKING_ID);
		if (placeholder) {
			placeholder.id = messageId;
			placeholder.status = "streaming";
			placeholder.content = "";
			return placeholder;
		}
		m = { id: messageId, role: "assistant", status: "streaming", content: "" };
		messages.value.push(m);
		return m;
	}

	function dropThinking() {
		const i = messages.value.findIndex((x) => x.id === THINKING_ID);
		if (i >= 0) messages.value.splice(i, 1);
	}

	async function submitPrompt(extra = {}) {
		const text = prompt.value.trim();
		if (!text || isRunning.value) return;
		messages.value.push({ id: `u-${Date.now()}`, role: "user", content: text });
		messages.value.push({
			id: THINKING_ID,
			role: "assistant",
			status: "streaming",
			content: "",
		});
		prompt.value = "";
		isRunning.value = true;
		errorText.value = "";
		lastSubmit = { text, extra };
		const atts = attachments.value;
		const projectChip = atts.find((a) => a.type === "project");
		const docChip = atts.find((a) => a.type === "document");
		const scope = scopeOf(atts);
		const project = projectChip?.name || null;
		const sourceDocument = docChip?.name || null;
		try {
			// Brand-new chat: create the session and subscribe to its realtime channel
			// BEFORE enqueuing the run. The loop runs on a worker and emits
			// wikify_agent_stream/complete:<sid> as it goes; Frappe realtime has no replay,
			// so if we only bind after `run` returns (once we learn the id) the worker can
			// emit before we're listening and the turn hangs on "Thinking…" forever even
			// though the backend finished. Binding is synchronous, so doing it before `run`
			// guarantees handlers exist before the job is enqueued.
			if (!sessionId.value) {
				const created = await call("wikify.api.agent.new_session", {
					scope,
					project,
					source_document: sourceDocument,
				});
				sessionId.value = created.session_id;
				rebind();
			}
			const res = await call("wikify.api.agent.run", {
				prompt: text,
				session_id: sessionId.value,
				scope,
				project,
				source_document: sourceDocument,
				model: model.value || null,
				attachments: atts.map(({ type, name, label }) => ({ type, name, label })),
				...extra,
			});
			sessionId.value = res.session_id;
			rebind();
		} catch (e) {
			isRunning.value = false;
			dropThinking();
			errorText.value = e?.messages?.[0] || e?.message || "Failed to start the assistant.";
		}
	}

	// Confirm card → re-run the held tool, this time pre-approved.
	async function approveTool(card) {
		if (isRunning.value) return;
		card.status = "approved";
		prompt.value = `Yes — go ahead and run ${card.toolName}.`;
		await submitPrompt({ approved_tools: [card.toolName] });
	}

	function dismissConfirm(card) {
		card.status = "dismissed";
	}

	// Clarification chip → answer with the chosen option.
	async function selectClarifyOption(option) {
		if (isRunning.value) return;
		prompt.value = option;
		await submitPrompt();
	}

	// Error → retry the same prompt + extras (re-run a held confirm, an option, a turn).
	async function retry() {
		if (!lastSubmit || isRunning.value) return;
		messages.value = messages.value.filter((m) => m.status !== "error");
		errorText.value = "";
		prompt.value = lastSubmit.text;
		await submitPrompt(lastSubmit.extra);
	}

	async function cancel() {
		if (!sessionId.value) return;
		await call("wikify.api.agent.cancel", { session_id: sessionId.value });
		isRunning.value = false;
		dropThinking();
	}

	async function loadSession(id) {
		const res = await call("wikify.api.agent.get_session", { session_id: id });
		sessionId.value = id;
		messages.value = hydrate(res.messages || []);
		isRunning.value = !!res.session?.is_running;
		if (res.session?.model) model.value = res.session.model;
		errorText.value = "";
		rebind();
	}

	async function listSessions() {
		sessions.value = await call("wikify.api.agent.list_sessions", {});
		return sessions.value;
	}

	async function loadModels() {
		if (models.value.length) return models.value;
		models.value = await call("wikify.api.agent.get_agent_models", {});
		// Pin the picker to the resolved default so a turn always sends a concrete model.
		if (!model.value && models.value.length) model.value = models.value[0];
		return models.value;
	}

	async function renameSession(title) {
		if (!sessionId.value || !title.trim()) return;
		await call("wikify.api.agent.rename_session", { session_id: sessionId.value, title });
		const s = sessions.value.find((x) => x.name === sessionId.value);
		if (s) s.title = title.trim();
	}

	async function archiveSession() {
		if (!sessionId.value) return;
		await call("wikify.api.agent.archive_session", { session_id: sessionId.value });
		newSession();
		await listSessions();
	}

	function newSession() {
		unbind?.();
		unbind = null;
		sessionId.value = null;
		messages.value = [];
		errorText.value = "";
		isRunning.value = false;
		// Re-seed the chip row from the current surface's defaults.
		attachments.value = (defaultAttachments.value || []).map((a) => ({ ...a }));
	}

	return {
		messages,
		prompt,
		sessionId,
		isRunning,
		errorText,
		sessions,
		model,
		models,
		attachments,
		removeAttachment,
		submitPrompt,
		approveTool,
		dismissConfirm,
		selectClarifyOption,
		retry,
		cancel,
		loadSession,
		listSessions,
		loadModels,
		renameSession,
		archiveSession,
		newSession,
	};
}

function hydrate(rows) {
	const out = [];
	for (const r of rows) {
		if (r.role === "tool") {
			out.push({
				id: r.name,
				role: "tool",
				toolName: r.tool_name,
				status: "done",
				content: r.content,
			});
		} else if (r.role === "assistant") {
			// Assistant rows that only carried tool calls (no text) are represented by their
			// tool cards; skip the empty bubble.
			if ((r.content || "").trim()) {
				out.push({
					id: r.name,
					role: "assistant",
					status: r.status || "done",
					content: r.content,
				});
			}
		} else if (r.role === "user") {
			out.push({ id: r.name, role: "user", content: r.content });
		}
	}
	return out;
}
