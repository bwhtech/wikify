<script setup>
import { Button, PageHeader, formatShortcutLabel } from "frappe-ui";
import { useIsMobile } from "@/composables/useMediaQuery";
import { PALETTE_SHORTCUT, paletteOpen } from "@/data/commandPalette";
import { useTheme } from "@/utils/useTheme";

const isMobile = useIsMobile();
const { resolvedTheme, toggleTheme } = useTheme();
const shortcutLabel = formatShortcutLabel(PALETTE_SHORTCUT);
</script>

<template>
	<PageHeader>
		<template v-if="isMobile">
			<slot />
			<slot name="actions" />
		</template>
		<div v-else class="flex w-full items-center gap-3">
			<div class="flex min-w-0 flex-1 basis-0 items-center">
				<slot />
			</div>
			<Button
				variant="subtle"
				class="w-full min-w-32 max-w-md !shrink !justify-start !text-ink-gray-5"
				label="Search"
				@click="paletteOpen = true"
			>
				<template #prefix>
					<span class="lucide-search size-4" aria-hidden="true" />
				</template>
				Search
				<template #suffix>
					<span class="ml-auto text-sm" aria-hidden="true">{{ shortcutLabel }}</span>
				</template>
			</Button>
			<div class="flex min-w-max flex-1 basis-0 items-center justify-end gap-2">
				<slot name="actions" />
				<Button
					variant="ghost"
					:icon="resolvedTheme === 'dark' ? 'lucide-sun' : 'lucide-moon'"
					:tooltip="resolvedTheme === 'dark' ? 'Light mode' : 'Dark mode'"
					:label="resolvedTheme === 'dark' ? 'Light mode' : 'Dark mode'"
					@click="toggleTheme"
				/>
			</div>
		</div>
	</PageHeader>
</template>
