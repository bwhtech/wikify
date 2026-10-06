import { dialog, toast } from "frappe-ui";

// `deleteCall` is the `delete` useCall of a Wikify Import useList or useDoc.
export function confirmDeleteDocument({ title, deleteCall, params, onDeleted }) {
	dialog.danger({
		title: "Delete document",
		message: `Delete "${title}"? Its pages, sections and edits are removed for good. Published wiki pages stay.`,
		confirmLabel: "Delete",
		async onConfirm() {
			await deleteCall.submit(params);
			if (deleteCall.error) {
				throw new Error(
					deleteCall.error?.messages?.[0] || "Could not delete the document"
				);
			}
			toast.success("Document deleted");
			onDeleted?.();
		},
	});
}
