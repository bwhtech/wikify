<script setup>
import { ref, watch } from "vue";
import { Dialog, Button, ErrorMessage, useCall } from "frappe-ui";
import "cropperjs";

const props = defineProps({
	// { sourceDocument, pageNo, pageImage, caption, occurrence }
	target: { type: Object, required: true },
});
const emit = defineEmits(["close", "saved"]);

const open = ref(true);
watch(open, (isOpen) => {
	if (!isOpen) emit("close");
});

const canvasElement = ref(null);
const imageElement = ref(null);
const selectionElement = ref(null);
const cropperReady = ref(false);
let imageBounds = null;

function fitImageInCanvas(element, image) {
	const canvasWidth = canvasElement.value.offsetWidth;
	const canvasHeight = canvasElement.value.offsetHeight;
	const scale = Math.min(canvasWidth / image.naturalWidth, canvasHeight / image.naturalHeight);
	// $setTransform is a no-op unless one of the transform flags is on
	element.scalable = true;
	element.$setTransform(
		scale,
		0,
		0,
		scale,
		(canvasWidth - image.naturalWidth) / 2,
		(canvasHeight - image.naturalHeight) / 2
	);
	element.scalable = false;
	const width = image.naturalWidth * scale;
	const height = image.naturalHeight * scale;
	return { left: (canvasWidth - width) / 2, top: (canvasHeight - height) / 2, width, height };
}

watch(imageElement, async (element) => {
	cropperReady.value = false;
	if (!element) return;
	const image = await element.$ready();
	imageBounds = fitImageInCanvas(element, image);
	selectionElement.value.$change(
		Math.round(imageBounds.left + imageBounds.width / 4),
		Math.round(imageBounds.top + imageBounds.height / 4),
		Math.round(imageBounds.width / 2),
		Math.round(imageBounds.height / 2)
	);
	cropperReady.value = true;
});

function keepSelectionInsideImage(event) {
	if (!imageBounds) return;
	const left = Math.ceil(imageBounds.left);
	const top = Math.ceil(imageBounds.top);
	const right = Math.floor(imageBounds.left + imageBounds.width);
	const bottom = Math.floor(imageBounds.top + imageBounds.height);
	const { x, y, width, height } = event.detail;
	if (x >= left && y >= top && x + width <= right && y + height <= bottom) return;

	event.preventDefault();
	const selection = event.target;
	if (width === selection.width && height === selection.height) {
		selection.$change(
			Math.min(Math.max(x, left), right - width),
			Math.min(Math.max(y, top), bottom - height)
		);
		return;
	}
	const clampedLeft = Math.max(x, left);
	const clampedTop = Math.max(y, top);
	selection.$change(
		clampedLeft,
		clampedTop,
		Math.min(x + width, right) - clampedLeft,
		Math.min(y + height, bottom) - clampedTop
	);
}

const cropFigure = useCall({
	url: "/api/v2/method/wikify.api.pages.crop_page_figure",
	method: "POST",
	immediate: false,
});
async function submitCrop() {
	if (!cropperReady.value) return;
	const bounds = imageBounds;
	const selection = selectionElement.value;
	await cropFigure.submit({
		source_document: props.target.sourceDocument,
		page_no: props.target.pageNo,
		caption: props.target.caption,
		occurrence: props.target.occurrence,
		x0: (selection.x - bounds.left) / bounds.width,
		y0: (selection.y - bounds.top) / bounds.height,
		x1: (selection.x + selection.width - bounds.left) / bounds.width,
		y1: (selection.y + selection.height - bounds.top) / bounds.height,
	});
	if (cropFigure.error) return;
	emit("saved");
	open.value = false;
}
</script>

<template>
	<Dialog v-model:open="open" :title="`Fix '${target.caption}'`" size="xl">
		<template #body-content>
			<div class="overflow-hidden rounded border border-outline-gray-1">
				<cropper-canvas
					v-if="target.pageImage"
					ref="canvasElement"
					background
					class="block h-[60vh]"
				>
					<cropper-image ref="imageElement" :src="target.pageImage" alt="" />
					<cropper-shade hidden />
					<cropper-selection
						ref="selectionElement"
						movable
						resizable
						keyboard
						outlined
						@change="keepSelectionInsideImage"
					>
						<cropper-grid role="grid" bordered covered />
						<cropper-handle action="move" theme-color="rgba(255, 255, 255, 0.35)" />
						<cropper-handle action="n-resize" />
						<cropper-handle action="e-resize" />
						<cropper-handle action="s-resize" />
						<cropper-handle action="w-resize" />
						<cropper-handle action="ne-resize" />
						<cropper-handle action="nw-resize" />
						<cropper-handle action="se-resize" />
						<cropper-handle action="sw-resize" />
					</cropper-selection>
				</cropper-canvas>
				<p v-else class="p-6 text-center text-sm text-ink-gray-5">
					No page photo available to crop.
				</p>
			</div>
			<ErrorMessage :message="cropFigure.error" class="mt-2" />
			<div class="mt-3 flex justify-end gap-2">
				<Button label="Cancel" variant="ghost" @click="open = false" />
				<Button
					label="Crop &amp; embed"
					variant="solid"
					:loading="cropFigure.loading"
					:disabled="!cropperReady"
					@click="submitCrop"
				/>
			</div>
		</template>
	</Dialog>
</template>
