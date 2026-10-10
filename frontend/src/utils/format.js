export function pageRange(s) {
	if (s.page_start == null) return "";
	return s.page_start === s.page_end ? `p${s.page_start}` : `p${s.page_start}–${s.page_end}`;
}

export function plural(n, word, pluralWord = `${word}s`) {
	return `${n} ${n === 1 ? word : pluralWord}`;
}
