<script setup>
import { ref } from "vue";
import { useRouter } from "vue-router";
import { Dialog, FileUploader, FormControl, Button, ErrorMessage, useCall } from "frappe-ui";

const open = defineModel("open", { type: Boolean, default: false });

const router = useRouter();
const pdfUrl = ref(null);
const pdfName = ref("");
const title = ref("");

const startImport = useCall({
	url: "/api/v2/method/wikify.api.imports.start_import",
	method: "POST",
	immediate: false,
	onSuccess(name) {
		reset();
		open.value = false;
		router.push({ name: "ImportDetail", params: { name } });
	},
});

function onUpload(file) {
	pdfUrl.value = file.file_url;
	pdfName.value = file.file_name;
	// Default the title to the filename without extension; stays editable.
	if (!title.value) {
		title.value = (file.file_name || "").replace(/\.pdf$/i, "");
	}
}

function start() {
	if (!pdfUrl.value || !title.value) return;
	startImport.submit({ pdf_file_url: pdfUrl.value, title: title.value });
}

function reset() {
	pdfUrl.value = null;
	pdfName.value = "";
	title.value = "";
	startImport.reset();
}
</script>

<template>
	<Dialog v-model:open="open" title="New Import" @close="reset">
		<template #default>
			<div class="space-y-4">
				<div>
					<span class="mb-1.5 block text-xs text-ink-gray-5">PDF</span>
					<FileUploader
						:file-types="'application/pdf'"
						:upload-args="{ private: true }"
						@success="onUpload"
					>
						<template #default="{ openFileSelector, uploading, progress }">
							<div class="flex items-center gap-3">
								<Button
									:loading="uploading"
									:label="
										uploading
											? `Uploading ${progress}%`
											: pdfUrl
												? 'Replace PDF'
												: 'Choose PDF'
									"
									icon-left="lucide-upload"
									@click="openFileSelector"
								/>
								<span v-if="pdfName" class="truncate text-sm text-ink-gray-7">{{
									pdfName
								}}</span>
							</div>
						</template>
					</FileUploader>
				</div>

				<FormControl
					v-model="title"
					label="Title"
					type="text"
					placeholder="Import title"
					:disabled="!pdfUrl"
				/>

				<ErrorMessage :message="startImport.error?.message" />
			</div>
		</template>

		<template #actions>
			<Button
				variant="solid"
				theme="gray"
				label="Start"
				:loading="startImport.loading"
				:disabled="!pdfUrl || !title"
				@click="start"
			/>
		</template>
	</Dialog>
</template>
