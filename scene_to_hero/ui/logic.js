(function (global) {
  // Project fields preserved by the UI.
  const known = ["id", "path", "order", "importance", "role", "note"];
  const scenePathVectors = {
    "reject": [
      "/etc/hosts", "../x.png", "scenes/../../x.png", "C:/x.png",
      "\\\\srv\\x.png", "a\\b.png", "", ".", "scenes/", "project.json",
      "\u0000x.png", "\t/x.png"
    ],
    "accept": ["scenes/a.png", "scenes/sub/b.jpg", "scenes/A.JPEG", "./scenes/a.webp"]
  };
  const sceneSuffix = /\.(png|jpg|jpeg|webp)$/i;
  const namePattern = /^[A-Za-z0-9._-]+$/;

  function hasControl(value) { return /[\u0000-\u001f]/.test(value); }

  function isValidScenePath(path) {
    if (typeof path !== "string" || hasControl(path) || !path || path.includes("\\") || path.startsWith("/") || /^[A-Za-z]:/.test(path)) return false;
    const segments = path.split("/");
    return !segments.includes("..") && segments.some((segment) => segment !== "" && segment !== ".") && sceneSuffix.test(segments[segments.length - 1]);
  }

  function parseProject(text) {
    let project;
    try { project = JSON.parse(text); } catch (_) { throw Error("project.json is not valid JSON"); }
    if (!project || typeof project !== "object" || Array.isArray(project)) throw Error("project must be an object");
    if (!Array.isArray(project.scenes)) throw Error("project.scenes must be an array");
    return project;
  }

  function validateProject(project) {
    const errors = [], warnings = [];
    if (!project || typeof project !== "object" || typeof project.name !== "string" || !namePattern.test(project.name) || project.name === "." || project.name === "..") errors.push("project name is invalid");
    const scenes = Array.isArray(project && project.scenes) ? project.scenes : [];
    const ids = scenes.map((scene) => scene.id), orders = scenes.map((scene) => scene.order);
    if (new Set(ids).size !== ids.length) errors.push("scene ids must be unique");
    if (new Set(orders).size !== orders.length) errors.push("scene orders must be unique");
    scenes.forEach((scene) => {
      if (!isValidScenePath(scene.path)) errors.push("scene path is invalid");
      if (!Number.isInteger(scene.importance) || scene.importance < 1 || scene.importance > 5) errors.push("importance must be an integer from 1 to 5");
      if (scene.role !== "final" && scene.role !== "reference") errors.push("scene role must be final or reference");
    });
    const finalCount = scenes.filter((scene) => scene.role === "final").length;
    if (finalCount > 1) errors.push("there can be at most one final scene");
    if (!finalCount) warnings.push("no final scene is set");
    return { errors, warnings };
  }

  function sortedScenes(scenes) { return [...scenes].sort((a, b) => a.order - b.order); }

  function moveScene(scenes, from, to) {
    const result = scenes.map((scene) => ({ ...scene }));
    if (from >= 0 && from < result.length && to >= 0 && to < result.length) {
      const [scene] = result.splice(from, 1); result.splice(to, 0, scene);
    }
    return result.map((scene, index) => ({ ...scene, order: index }));
  }

  function moveSceneBy(scenes, id, delta) {
    const index = scenes.findIndex((scene) => scene.id === id);
    return index < 0 ? scenes.map((scene) => ({ ...scene })) : moveScene(scenes, index, Math.max(0, Math.min(scenes.length - 1, index + delta)));
  }

  function setImportance(scenes, id, value) {
    if (!Number.isInteger(value) || value < 1 || value > 5) throw RangeError("importance must be an integer from 1 to 5");
    return scenes.map((scene) => (scene.id === id ? { ...scene, importance: value } : { ...scene }));
  }
  function setNote(scenes, id, text) { return scenes.map((scene) => (scene.id === id ? { ...scene, note: text } : { ...scene })); }
  function setFinal(scenes, id) { return scenes.map((scene) => ({ ...scene, role: scene.id === id ? "final" : "reference" })); }

  function serializeProject(original, scenes) {
    const serialized = scenes.map((scene, index) => {
      const output = {};
      known.forEach((key) => { if (Object.prototype.hasOwnProperty.call(scene, key)) output[key] = key === "order" ? index : scene[key]; });
      Object.keys(scene).forEach((key) => { if (!known.includes(key)) output[key] = scene[key]; });
      return output;
    });
    const ordered = {};
    Object.keys(original).forEach((key) => { ordered[key] = key === "scenes" ? serialized : original[key]; });
    if (!Object.prototype.hasOwnProperty.call(ordered, "scenes")) ordered.scenes = serialized;
    return JSON.stringify(ordered, null, 2) + "\n";
  }

  function safeProjectUrl(value, baseHref) {
    if (value === "") return { url: null, warning: null };
    if (typeof value !== "string" || hasControl(value) || value.includes("\\") || /^[A-Za-z][A-Za-z0-9+.-]*:/.test(value) || value.startsWith("//")) {
      return { url: null, warning: "ignored ?project= from another origin" };
    }
    try {
      const resolved = new URL(value, baseHref);
      if (resolved.origin !== new URL(baseHref).origin) return { url: null, warning: "ignored ?project= from another origin" };
      return { url: value, warning: null };
    } catch (_) {
      return { url: null, warning: "ignored ?project= from another origin" };
    }
  }

  function defaultProjectUrl(search, served) {
    const project = new URLSearchParams(search).get("project");
    const baseHref = typeof global.location === "object" && global.location.href ? global.location.href : "http://localhost/";
    const safe = safeProjectUrl(project === null ? "" : project, baseHref);
    return { url: safe.url || (served ? "/project/project.json" : null), warning: safe.warning };
  }

  function saveRequest(original, scenes) {
    return { url: "/api/project", init: { method: "PUT", headers: { "Content-Type": "application/json" }, body: serializeProject(original, scenes) } };
  }

  function resolveSceneUrl(projectUrl, scenePath, baseHref) {
    if (typeof scenePath !== "string") return null;
    let hasParentSegment = false;
    try { hasParentSegment = scenePath.split("/").some((segment) => decodeURIComponent(segment) === ".."); } catch (_) { hasParentSegment = true; }
    if (hasControl(scenePath) || scenePath.includes("\\") || scenePath.startsWith("/") || /^[A-Za-z][A-Za-z0-9+.-]*:/.test(scenePath) || hasParentSegment) return null;
    try {
      const base = new URL(baseHref);
      const project = new URL(projectUrl, base);
      const resolved = new URL(scenePath, project);
      const directory = project.pathname.replace(/[^/]*$/, "");
      if (resolved.origin !== base.origin || !resolved.pathname.startsWith(directory)) return null;
      return resolved.href;
    } catch (_) { return null; }
  }

  function matchImageFiles(scenes, files) {
    const output = {};
    scenes.forEach((scene) => {
      const file = files.find((item) => item.webkitRelativePath && item.webkitRelativePath.endsWith(scene.path)) || files.find((item) => item.name === scene.path.split("/").pop());
      if (file) output[scene.id] = file;
    });
    return output;
  }

  const SceneUI = { parseProject, validateProject, sortedScenes, moveScene, moveSceneBy, setImportance, setNote, setFinal, serializeProject, defaultProjectUrl, safeProjectUrl, saveRequest, resolveSceneUrl, matchImageFiles, scenePathVectors };
  global.SceneUI = SceneUI;
  if (typeof module !== "undefined" && module.exports) module.exports = SceneUI;
})(globalThis);
