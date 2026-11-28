import { reactive } from "vue";

export const agentPanel = reactive({ open: false, draft: null });

export function askAgent(draft) {
	agentPanel.draft = draft;
	agentPanel.open = true;
}
