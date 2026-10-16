// Toolbar icon (or Alt+J) opens the side panel. The on-page Fill button (content.js), the right-click menu and
// Alt+Shift+F ask this worker to fill. Right-click and the shortcut work on any site: Chrome grants this tab for
// the click (activeTab), and the worker injects the on-page button there too.
importScripts("idb.js", "lib.js");
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({ id: "jaf-fill", title: "Fill this page (Job Apply Filler)", contexts: ["page", "editable", "frame"] });
});

async function fillAnySite(tab) {
  if (!tab || !/^https:/.test(tab.url || "")) return;
  await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
  chrome.tabs.sendMessage(tab.id, { type: "jaf-run" }).catch(() => {});
}
chrome.contextMenus.onClicked.addListener((info, tab) => { if (info.menuItemId === "jaf-fill") fillAnySite(tab); });
chrome.commands.onCommand.addListener(async (cmd, tab) => { if (cmd === "fill-page") fillAnySite(tab || (await chrome.tabs.query({ active: true, currentWindow: true }))[0]); });

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (!msg || !sender.tab) return;
  if (msg.type === "jaf-fill") { fillFromPage(sender.tab, msg).then(reply, (e) => reply({ error: String(e.message || e) })); return true; }
  if (msg.type === "jaf-match") { matchKit(sender.tab).then(reply, () => reply({})); return true; }
  if (msg.type === "jaf-dash") { chrome.storage.local.get("dash").then(({ dash }) => (dash ? chrome.tabs.create({ url: dash }) : chrome.runtime.openOptionsPage())); return; }
  if (msg.type === "jaf-submitted") { jafLog({ page: msg.page, submitted: true, kit: msg.kit || "" }); return; }
});

let cache = { at: 0, roles: null };
async function rolesNow() {
  const root = await jafRoot(false);
  if (!root || root === "needs-permission") return { root };
  if (!cache.roles || Date.now() - cache.at > 60000) cache = { at: Date.now(), roles: await jafRoles(root) };
  return { root, roles: cache.roles };
}

async function matchKit(tab) {
  const { roles } = await rolesNow();
  if (!roles) return {};
  const i = jafMatch(roles, tab.url, tab.title);
  return i < 0 ? {} : { kit: roles[i].folder, company: jafCompany(roles[i]) };
}

async function fillFromPage(tab, msg) {
  const profile = await jafProfile();
  const notes = [];
  let resume = null, kit = "", company = "";
  const { root, roles } = await rolesNow();
  if (!root) notes.push("Folder not set: open the side panel (Alt+J) once.");
  else if (root === "needs-permission") notes.push("Folder access expired: open the side panel and click Allow, then Fill again for the resume.");
  else {
    const i = jafMatch(roles, tab.url, tab.title);
    if (i < 0) notes.push("No role kit matched this page: profile answers only, no resume. Pick the kit in the side panel to attach it.");
    else {
      kit = roles[i].folder;
      company = jafCompany(roles[i]);
      resume = await jafResume(roles[i]);
      if (!resume) notes.push(`No resume PDF in ${kit}.`);
    }
  }
  const { heard } = await chrome.storage.local.get("heard");
  const h = heard || { re: "job board", text: "Job board" };
  const results = await jafFill(tab.id, { profile, resume, opts: { heardRe: h.re, heard: h.text, company } });
  jafLog(jafLogEntry(tab.url, kit, results));
  return { results, notes, kit };
}
