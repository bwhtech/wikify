import { createRouter, createWebHistory } from "vue-router";
import { session } from "@/data/session";
import {
	DEFAULT_SETTINGS_TAB,
	SETTINGS_ROUTE_NAME,
	attachSettingsRouter,
	isSettingsTab,
} from "@/data/settingsRoute";

const routes = [
	{
		path: "/dashboard",
		name: "Dashboard",
		component: () => import("@/pages/Dashboard.vue"),
	},
	{
		path: "/",
		name: "Projects",
		component: () => import("@/pages/ProjectList.vue"),
	},
	{
		path: "/project/:name/settings",
		name: "ProjectSettings",
		component: () => import("@/components/ProjectSettings.vue"),
		props: true,
	},
	{
		path: "/project/:name/graph",
		name: "ProjectGraph",
		component: () => import("@/pages/ProjectGraph.vue"),
		props: true,
	},
	{
		path: "/project/:name",
		name: "ProjectDetail",
		component: () => import("@/pages/ProjectDetail.vue"),
		props: true,
	},
	{
		// Static segment outranks the :tab? param below, so /graph never lands there.
		path: "/import/:name/graph",
		name: "ImportGraph",
		component: () => import("@/pages/ImportGraph.vue"),
		props: true,
	},
	{
		// The Wiki tab used to be its own route; old links land on the merged tab.
		path: "/import/:name/wiki",
		redirect: (to) => ({
			name: "ImportDetail",
			params: { name: to.params.name, tab: "tree" },
			query: to.query,
		}),
	},
	{
		path: "/import/:name/:tab?",
		name: "ImportDetail",
		component: () => import("@/pages/ImportDetail.vue"),
		props: true,
	},
	{
		// The conversation id rides in the path so a refresh, a bookmark or a pasted link
		// reopens the thread rather than dropping it.
		path: "/ask/:conversationId?",
		name: "AskWiki",
		component: () => import("@/pages/AskWiki.vue"),
	},
	{
		path: "/explore",
		name: "Explore",
		component: () => import("@/pages/ExploreGlobal.vue"),
	},
	// No component: the page the dialog was opened over stays mounted behind it.
	{ path: "/settings", redirect: `/settings/${DEFAULT_SETTINGS_TAB}` },
	{
		path: "/settings/:tab",
		name: SETTINGS_ROUTE_NAME,
		beforeEnter: (to) =>
			isSettingsTab(to.params.tab) || {
				path: `/settings/${DEFAULT_SETTINGS_TAB}`,
				replace: true,
			},
	},
];

// __FRONTEND_ROUTE__ is injected by the frappe-ui vite plugin (= '/wikify').
const router = createRouter({
	history: createWebHistory(__FRONTEND_ROUTE__ + "/"),
	routes,
});

router.beforeEach((to, from, next) => {
	if (!session.isLoggedIn) {
		// Not authenticated — hand off to the Frappe login screen.
		window.location.href = "/login?redirect-to=/wikify";
		return;
	}
	next();
});

attachSettingsRouter(router);

export default router;
