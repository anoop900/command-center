const DEFAULT_DASH = "";
const st = document.getElementById("status");
(async () => {
  const h = await IDB.get("root");
  st.textContent = h ? `Folder set: ${h.name}` : "No folder chosen yet.";
  const { dash, kitsFolder } = await chrome.storage.local.get(["dash", "kitsFolder"]);
  document.getElementById("dash").value = dash || DEFAULT_DASH;
  document.getElementById("kits").value = kitsFolder || "Daily";
})();
document.getElementById("pick").onclick = async () => {
  try {
    const h = await window.showDirectoryPicker({ id: "jobhunting", mode: "read" });
    const kits = document.getElementById("kits").value.trim() || "Daily";
    try { await h.getDirectoryHandle(kits); } catch { st.textContent = `"${h.name}" has no ${kits} folder. Pick the folder that holds it.`; return; }
    await IDB.set("root", h);
    st.textContent = `Folder set: ${h.name}`;
  } catch (e) { st.textContent = "Not changed: " + e.message; }
};
document.getElementById("save").onclick = async () => {
  await chrome.storage.local.set({ dash: document.getElementById("dash").value.trim() || DEFAULT_DASH, kitsFolder: document.getElementById("kits").value.trim() || "Daily" });
  st.textContent = "Saved.";
};
