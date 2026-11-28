<script setup>
import { Tooltip } from "frappe-ui";
import { TILE_CLASSES, TILE_LEGEND } from "@/components/review/meta";

defineProps({ tiles: { type: Array, default: () => [] } });
const emit = defineEmits(["open", "open-page"]);

const STATE_LABELS = Object.fromEntries(TILE_LEGEND.map((l) => [l.state, l.label]));

function open(tile) {
	if (tile.issue_id) emit("open", tile.issue_id);
	else emit("open-page", tile.page_no);
}
</script>

<template>
	<section class="space-y-3">
		<div class="flex flex-wrap items-baseline justify-between gap-2">
			<h2 class="text-lg font-semibold text-ink-gray-8">Page map</h2>
			<div class="flex flex-wrap gap-x-3 gap-y-1">
				<span
					v-for="item in TILE_LEGEND"
					:key="item.state"
					class="flex items-center gap-1.5 text-xs text-ink-gray-5"
				>
					<span
						class="size-2 rounded-full"
						:class="TILE_CLASSES[item.state]"
						aria-hidden="true"
					/>
					{{ item.label }}
				</span>
			</div>
		</div>
		<div
			class="grid gap-1"
			style="grid-template-columns: repeat(auto-fill, minmax(14px, 1fr))"
		>
			<Tooltip
				v-for="tile in tiles"
				:key="tile.page_no"
				:text="`Page ${tile.page_no}: ${STATE_LABELS[tile.state]}`"
			>
				<button
					class="block aspect-[3/4] w-full rounded-[2px] transition-transform hover:scale-125"
					:class="TILE_CLASSES[tile.state]"
					:aria-label="`Page ${tile.page_no}: ${STATE_LABELS[tile.state]}`"
					@click="open(tile)"
				/>
			</Tooltip>
		</div>
	</section>
</template>
