import type { Locator, Page } from "@playwright/test";
import type { Api, Row } from "./api";
import { waitFor } from "./wait";

// The panel's icon-only buttons have no accessible name (frappe-ui sets aria-label from `label` only).
export function iconButton(page: Page, icon: string, scope: Page | Locator = page): Locator {
	return scope.getByRole("button").filter({ has: page.locator(`.${icon}`) });
}

// is_running alone is not enough: run() commits the title a moment before it sets is_running.
// Returns the rows the turn added after the prompt.
export async function waitForTurn(
	api: Api,
	sessionId: string,
	prompt: string,
	timeout: number,
): Promise<Row[]> {
	const { messages } = await waitFor(
		() => api.call("wikify.api.agent.get_session", { session_id: sessionId }),
		({ session, messages }) => {
			const last = messages.at(-1);
			return (
				!session.is_running &&
				last?.role === "assistant" &&
				last.status !== "streaming" &&
				messages.some((row: Row) => row.content === prompt)
			);
		},
		{ timeout, interval: 3_000, label: `assistant turn in ${sessionId}` },
	);
	const start = messages.findLastIndex(
		(row: Row) => row.role === "user" && row.content === prompt,
	);
	return messages.slice(start + 1);
}
