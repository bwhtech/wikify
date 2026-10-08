export const DESTINATIONS = [
	{
		label: "Dashboard",
		icon: "lucide-layout-dashboard",
		route: "Dashboard",
		activeOn: ["Dashboard"],
	},
	{
		label: "Projects",
		icon: "lucide-folder",
		route: "Projects",
		activeOn: ["Projects", "ProjectDetail", "ProjectSettings", "ImportDetail"],
	},
	{ label: "Explore", icon: "lucide-shapes", route: "Explore", activeOn: ["Explore"] },
	{
		label: "Ask",
		icon: "lucide-message-circle-question",
		route: "AskWiki",
		activeOn: ["AskWiki"],
	},
];
