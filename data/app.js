const state = {
  query: "",
  genreFilters: {
    movies: "",
    tv: "",
    audiobooks: "",
  },
  route: parseRoute(window.location.pathname),
  status: null,
  library: null,
  librarySource: "",
  playbackLoaded: false,
  playbackProfile: null,
  playingItem: null,
  playbackProgress: {},
  watchedOverrides: {},
  pendingResume: null,
  lastProgressSaveAt: 0,
  uploadDraft: {
    destination: "/media/tv",
    newFolder: "",
  },
  uploadFeedback: "",
  uploadFeedbackTone: "",
  deviceConfigDraft: {
    deviceName: "",
    hotspotSsid: "",
    wifiPassword: "",
    tmdbApiKey: "",
  },
  catalogSummary: null,
  catalogGenres: {
    movies: [],
    tv: [],
    audiobooks: [],
  },
  catalogHome: null,
  catalogProgress: {
    count: 0,
    showCount: 0,
    items: [],
    shows: [],
    loaded: false,
  },
  catalogSearch: {
    query: "",
    count: 0,
    items: [],
    loaded: false,
  },
  catalogMovies: {
    query: "",
    genre: "",
    total: 0,
    offset: 0,
    limit: 40,
    count: 0,
    hasMore: false,
    items: [],
    loaded: false,
    loadingMore: false,
  },
  catalogShows: {
    query: "",
    genre: "",
    total: 0,
    offset: 0,
    limit: 40,
    count: 0,
    hasMore: false,
    items: [],
    loaded: false,
    loadingMore: false,
  },
  catalogAudiobooks: {
    query: "",
    genre: "",
    collection: "",
    author: "",
    total: 0,
    offset: 0,
    limit: 40,
    count: 0,
    hasMore: false,
    items: [],
    loaded: false,
    loadingMore: false,
  },
  catalogMovie: null,
  catalogMovieLoaded: false,
  catalogAudiobook: null,
  catalogAudiobookLoaded: false,
  catalogShow: null,
  catalogShowLoaded: false,
  deviceConfigFeedback: "",
  deviceConfigFeedbackTone: "",
  deviceConfigLoaded: false,
  preferServerLibrary: false,
  uploadingLocally: false,
  uploadSelectionLocked: false,
  uploadDestinations: [],
};

const NOMAD_STORAGE_PREFIX = "nomadscreen-";
const PLAYBACK_BACKUP_KEY = `${NOMAD_STORAGE_PREFIX}playback-cache-v1`;
const PROGRESS_SAVE_INTERVAL_MS = 5000;
const RESUME_MIN_SECONDS = 30;
const CATALOG_PAGE_SIZE = 40;
const CATALOG_SEARCH_LIMIT = 60;
const UPLOAD_ROOTS = [
  {
    value: "movies",
    label: "Movies",
    root: "/media/movies",
    newFolderPlaceholder: "Favorites",
    help: "Upload standalone video files here.",
  },
  {
    value: "tv",
    label: "TV Shows",
    root: "/media/tv",
    newFolderPlaceholder: "Show Name/Season 1",
    help: "Use show and season folders so episodes stay grouped correctly.",
  },
  {
    value: "music",
    label: "Music",
    root: "/media/music",
    newFolderPlaceholder: "Artist/Album",
    help: "Use folders like Artist/Album for cleaner browsing.",
  },
  {
    value: "audiobooks",
    label: "Audiobooks",
    root: "/media/audiobooks",
    newFolderPlaceholder: "Author/Series",
    help: "Use folders to keep books and series organized.",
  },
  {
    value: "documents",
    label: "Documents",
    root: "/media/documents",
    newFolderPlaceholder: "Maps/Trip Name",
    help: "Great for PDFs, images, maps, permits, and checklists.",
  },
];

const els = {
  brandMark: document.getElementById("brand-mark"),
  brandTitle: document.getElementById("brand-title"),
  breadcrumbs: document.getElementById("breadcrumbs"),
  pageEyebrow: document.getElementById("page-eyebrow"),
  pageTitle: document.getElementById("page-title"),
  pageSubtitle: document.getElementById("page-subtitle"),
  pageTools: document.querySelector(".page-tools"),
  search: document.getElementById("search-input"),
  pageFilterSlot: document.getElementById("page-filter-slot"),
  actions: document.getElementById("page-actions"),
  hero: document.getElementById("hero-spotlight"),
  content: document.getElementById("page-content"),
  playerCaption: document.getElementById("player-caption"),
  playerCard: document.querySelector(".player-card"),
  playerTitle: document.getElementById("player-title"),
  playerSummary: document.getElementById("player-summary"),
  playerActions: document.getElementById("player-actions"),
  playerFacts: document.getElementById("player-facts"),
  video: document.getElementById("video-player"),
  audio: document.getElementById("audio-player"),
  document: document.getElementById("document-viewer"),
  image: document.getElementById("image-player"),
  empty: document.getElementById("empty-player"),
  cardTemplate: document.getElementById("library-card-template"),
  episodeTemplate: document.getElementById("episode-row-template"),
  nav: Array.from(document.querySelectorAll(".nav-link")),
};

let deviceStatusPollTimer = 0;
let deviceStatusPollInFlight = false;
let lastCompletedUploadRefreshKey = "";
let uploadDestinationsRequest = null;
let deviceConfigRequest = null;
let catalogGenresRequest = null;
let liveUploadActivityTargets = new Set();
let liveMetadataActivityTargets = new Set();
let routeQueryRefreshTimer = 0;
let catalogAutoLoadObserver = null;
let playbackStateRequest = null;
let playbackSyncQueue = Promise.resolve();

function parseRoute(pathname) {
  const cleanPath = pathname.replace(/\/+$/, "") || "/";
  const parts = cleanPath.split("/").filter(Boolean);

  if (!parts.length || parts[0] !== "app") {
    return { name: "home" };
  }

  if (parts.length === 1) {
    return { name: "home" };
  }

  if (parts[1] === "movies") {
    return { name: "movies" };
  }

  if (parts[1] === "movie" && parts[2]) {
    return { name: "movie", path: decodeURIComponent(parts.slice(2).join("/")) };
  }

  if (parts[1] === "tv" && parts[2] && parts[3] === "season" && parts[4]) {
    return {
      name: "season",
      slug: decodeURIComponent(parts[2]),
      seasonKey: decodeURIComponent(parts.slice(4).join("/")),
    };
  }

  if (parts[1] === "tv" && parts[2]) {
    return { name: "show", slug: decodeURIComponent(parts.slice(2).join("/")) };
  }

  if (parts[1] === "tv") {
    return { name: "tv" };
  }

  if (parts[1] === "music") {
    return { name: "music" };
  }

  if (parts[1] === "audiobooks") {
    if (parts[2] === "detail" && parts[3]) {
      return { name: "audiobook", path: decodeURIComponent(parts.slice(3).join("/")) };
    }
    if (parts[2] === "collection" && parts[3]) {
      return { name: "audiobookCollection", label: decodeURIComponent(parts.slice(3).join("/")) };
    }
    if (parts[2] === "author" && parts[3]) {
      return { name: "audiobookAuthor", label: decodeURIComponent(parts.slice(3).join("/")) };
    }
    return { name: "audiobooks" };
  }

  if (parts[1] === "documents" || parts[1] === "photos") {
    return {
      name: "documents",
      folder: normalizeDocumentFolder(parts.slice(2).map((part) => decodeURIComponent(part)).join("/")),
    };
  }

  if (parts[1] === "device") {
    return { name: "device" };
  }

  return { name: "home" };
}

function buildRoutePath(route) {
  if (route.name === "movies") return "/app/movies";
  if (route.name === "movie") return `/app/movie/${encodeURIComponent(route.path)}`;
  if (route.name === "tv") return "/app/tv";
  if (route.name === "season") {
    return `/app/tv/${encodeURIComponent(route.slug)}/season/${encodeURIComponent(route.seasonKey)}`;
  }
  if (route.name === "show") return `/app/tv/${encodeURIComponent(route.slug)}`;
  if (route.name === "music") return "/app/music";
  if (route.name === "audiobook") return `/app/audiobooks/detail/${encodeURIComponent(route.path)}`;
  if (route.name === "audiobookCollection") return `/app/audiobooks/collection/${encodeURIComponent(route.label || "")}`;
  if (route.name === "audiobookAuthor") return `/app/audiobooks/author/${encodeURIComponent(route.label || "")}`;
  if (route.name === "audiobooks") return "/app/audiobooks";
  if (route.name === "documents") {
    const folder = normalizeDocumentFolder(route.folder);
    return folder
      ? `/app/documents/${folder.split("/").map((part) => encodeURIComponent(part)).join("/")}`
      : "/app/documents";
  }
  if (route.name === "device") return "/app/device";
  return "/app";
}

function currentAudiobookBrowseTarget(route = state.route) {
  if (route && route.name === "audiobookCollection") {
    return { kind: "collection", label: String(route.label || "").trim() };
  }
  if (route && route.name === "audiobookAuthor") {
    return { kind: "author", label: String(route.label || "").trim() };
  }
  return { kind: "", label: "" };
}

function isAudiobookBrowseRoute(route = state.route) {
  return ["audiobooks", "audiobookCollection", "audiobookAuthor"].includes(route && route.name);
}

function lowerPath(value) {
  return String(value || "").toLowerCase();
}

function extensionFromPath(path) {
  const match = String(path || "").match(/\.([^.\/]+)$/);
  return match ? match[1].toUpperCase() : "";
}

function fileNameFromPath(path) {
  return String(path || "").split("/").filter(Boolean).pop() || "";
}

function mediaTypeForPath(path) {
  const lower = lowerPath(path);
  if (/\.(mp4|mkv|mov|webm|m4v|avi)$/.test(lower)) return "video";
  if (/\.(mp3|m4a|m4b|aac|wav|flac|ogg)$/.test(lower)) return "audio";
  if (/\.(jpg|jpeg|png|gif|webp)$/.test(lower)) return "image";
  if (/\.(pdf|txt|md|csv|gpx|kml|doc|docx)$/.test(lower)) return "document";
  return "";
}

function sectionForPath(path, type) {
  const lower = lowerPath(path);
  if (lower.startsWith("/media/movies/")) return "movies";
  if (lower.startsWith("/media/tv/")) return "tv";
  if (lower.startsWith("/media/music/")) return "music";
  if (lower.startsWith("/media/audiobooks/")) return "audiobooks";
  if (lower.startsWith("/media/documents/")) return "documents";
  if (lower.startsWith("/media/photos/")) return "documents";
  if (type === "video") return "movies";
  if (type === "audio") return "music";
  if (type === "image" || type === "document") return "documents";
  return "library";
}

function titleFromPath(path) {
  const fileName = String(path || "").split("/").pop() || "";
  const stem = fileName.replace(/\.[^.]+$/, "");
  return stem.replace(/[_\.]+/g, " ").replace(/\s+/g, " ").trim() || stem || "Untitled";
}

function slugifyText(value) {
  return String(value || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "") || "unknown-show";
}

function streamServerOrigin() {
  const streamPort = Number((state.status && state.status.streamPort) || 81) || 81;
  const url = new URL(window.location.origin);
  url.port = String(streamPort);
  return url.origin;
}

function buildStreamUrl(path) {
  return path ? new URL(`/api/stream?path=${encodeURIComponent(path)}`, streamServerOrigin()).href : "";
}

function buildAssetUrl(path, versionToken) {
  if (!path) {
    return "";
  }

  const suffix = versionToken ? `&v=${encodeURIComponent(versionToken)}` : "";
  return `/api/asset?path=${encodeURIComponent(path)}${suffix}`;
}

function absoluteUrl(url) {
  return url ? new URL(url, window.location.origin).href : "";
}

function libraryIndexUrl() {
  const versionToken =
    (state.status && state.status.metadataGeneratedAt) ||
    (state.library && state.library.metadata && state.library.metadata.generatedAt) ||
    "";
  return absoluteUrl(buildAssetUrl("/media/.nomadscreen/library.json", versionToken));
}

function appDisplayName() {
  return (state.status && state.status.device) || "Media Server";
}

function appNetworkName() {
  return (state.status && (state.status.hotspotSsid || state.status.ssid)) || appDisplayName();
}

function sanitizeUploadSegments(value) {
  return String(value || "")
    .replace(/\\/g, "/")
    .split("/")
    .map((piece) => piece.trim())
    .filter((piece) => piece && piece !== "." && piece !== "..");
}

function normalizeUploadDestinationPath(value) {
  const segments = sanitizeUploadSegments(value);
  if (!segments.length || lowerPath(segments[0]) !== "media") {
    return "";
  }
  const normalized = `/${segments.join("/")}`;
  return UPLOAD_ROOTS.some((entry) => normalized === entry.root || normalized.startsWith(`${entry.root}/`))
    ? normalized
    : "";
}

function normalizeUploadSubfolder(value) {
  return sanitizeUploadSegments(value).join("/");
}

function uploadRootConfigForPath(path) {
  const normalized = normalizeUploadDestinationPath(path);
  return (
    UPLOAD_ROOTS.find((entry) => normalized === entry.root || normalized.startsWith(`${entry.root}/`)) ||
    UPLOAD_ROOTS[0]
  );
}

function uploadDestinationSuggestions() {
  const suggestions = new Set(UPLOAD_ROOTS.map((entry) => entry.root));
  for (const path of state.uploadDestinations || []) {
    const normalized = normalizeUploadDestinationPath(path);
    if (normalized) {
      suggestions.add(normalized);
    }
  }
  return Array.from(suggestions).sort((left, right) => {
    const leftConfig = uploadRootConfigForPath(left);
    const rightConfig = uploadRootConfigForPath(right);
    const leftDepth = sanitizeUploadSegments(left).length;
    const rightDepth = sanitizeUploadSegments(right).length;
    const leftOrder = UPLOAD_ROOTS.findIndex((entry) => entry.root === leftConfig.root);
    const rightOrder = UPLOAD_ROOTS.findIndex((entry) => entry.root === rightConfig.root);
    return leftOrder - rightOrder || leftDepth - rightDepth || left.localeCompare(right);
  });
}

function defaultUploadDestination() {
  return uploadDestinationSuggestions()[0] || UPLOAD_ROOTS[0].root;
}

function buildUploadDestination(destination, newFolder) {
  const normalizedDestination = normalizeUploadDestinationPath(destination);
  if (!normalizedDestination) {
    return "";
  }
  const subfolder = normalizeUploadSubfolder(newFolder);
  return subfolder ? `${normalizedDestination}/${subfolder}` : normalizedDestination;
}

function uploadDestinationPreview(destination, newFolder) {
  return buildUploadDestination(destination, newFolder);
}

function uploadDestinationHelp(destination) {
  const config = uploadRootConfigForPath(destination);
  return config ? config.help : "Choose a destination under /media and upload files there.";
}

function uploadDestinationTitle(path) {
  const normalized = normalizeUploadDestinationPath(path);
  if (!normalized) {
    return "";
  }
  const config = uploadRootConfigForPath(normalized);
  if (normalized === config.root) {
    return config.label;
  }
  return titleFromPath(normalized);
}

function uploadDestinationBreadcrumbs(path) {
  const normalized = normalizeUploadDestinationPath(path);
  if (!normalized) {
    return [];
  }

  const segments = sanitizeUploadSegments(normalized);
  const breadcrumbs = [];
  let current = "";
  for (let index = 0; index < segments.length; index += 1) {
    current += `/${segments[index]}`;
    if (index === 0) {
      continue;
    }
    const candidate = normalizeUploadDestinationPath(current);
    if (!candidate) {
      continue;
    }
    breadcrumbs.push({
      path: candidate,
      label: index === 1 ? uploadRootConfigForPath(candidate).label : titleFromPath(candidate),
    });
  }
  return breadcrumbs;
}

function uploadParentDestination(path) {
  const breadcrumbs = uploadDestinationBreadcrumbs(path);
  if (breadcrumbs.length <= 1) {
    return "";
  }
  return breadcrumbs[breadcrumbs.length - 2].path;
}

function uploadChildDestinations(path) {
  const normalized = normalizeUploadDestinationPath(path);
  if (!normalized) {
    return [];
  }

  const prefix = `${normalized}/`;
  const targetDepth = sanitizeUploadSegments(normalized).length + 1;
  return uploadDestinationSuggestions()
    .filter(
      (candidate) =>
        candidate !== normalized &&
        candidate.startsWith(prefix) &&
        sanitizeUploadSegments(candidate).length === targetDepth,
    )
    .sort((left, right) => uploadDestinationTitle(left).localeCompare(uploadDestinationTitle(right)));
}

function collectUploadEntries(looseFiles, folderFiles) {
  const entries = [];
  const seen = new Set();

  const pushEntry = (file, relativePath) => {
    if (!file) {
      return;
    }
    const normalizedRelativePath = sanitizeUploadSegments(relativePath || file.name).join("/") || file.name;
    const key = `${normalizedRelativePath}|${file.size}|${file.lastModified}`;
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    entries.push({ file, relativePath: normalizedRelativePath });
  };

  for (const file of Array.from(looseFiles || [])) {
    pushEntry(file, file.name);
  }
  for (const file of Array.from(folderFiles || [])) {
    pushEntry(file, file.webkitRelativePath || file.name);
  }
  return entries;
}

function describeUploadSelection(looseFiles, folderFiles) {
  const looseCount = Array.from(looseFiles || []).length;
  const folderEntries = Array.from(folderFiles || []);
  const topLevelFolders = new Set();
  for (const file of folderEntries) {
    const relativePath = String(file.webkitRelativePath || "");
    const topLevel = sanitizeUploadSegments(relativePath)[0];
    if (topLevel) {
      topLevelFolders.add(topLevel);
    }
  }

  return joinBits(
    [
      looseCount ? `${looseCount} loose file${looseCount === 1 ? "" : "s"}` : "",
      folderEntries.length
        ? `${topLevelFolders.size || 1} folder${topLevelFolders.size === 1 ? "" : "s"} (${folderEntries.length} files)`
        : "",
    ].filter(Boolean),
  );
}

function uploadStatusSnapshot() {
  return (state.status && state.status.upload) || null;
}

function uploadStatusPhase(upload) {
  return String((upload && upload.phase) || "idle").toLowerCase();
}

function uploadHasActivity(upload) {
  const phase = uploadStatusPhase(upload);
  return Boolean(upload && (upload.active || upload.id || phase === "completed" || phase === "error"));
}

function uploadPercent(upload) {
  const reported = Number(upload && upload.percent);
  if (Number.isFinite(reported) && reported >= 0) {
    return Math.max(0, Math.min(100, Math.round(reported)));
  }

  const bytesSent = Number((upload && upload.bytesSent) || 0);
  const bytesTotal = Number((upload && upload.bytesTotal) || 0);
  if (bytesTotal > 0) {
    return Math.max(0, Math.min(100, Math.round((bytesSent / bytesTotal) * 100)));
  }
  return 0;
}

function uploadPhaseLabel(upload) {
  const phase = uploadStatusPhase(upload);
  if (phase === "uploading") return "Uploading";
  if (phase === "processing") return "Processing";
  if (phase === "completed") return "Complete";
  if (phase === "error") return "Needs attention";
  return "Standing by";
}

function uploadPhaseTone(upload) {
  const phase = uploadStatusPhase(upload);
  if (phase === "completed") return "success";
  if (phase === "error") return "error";
  if (phase === "uploading" || phase === "processing") return "pending";
  return "";
}

function uploadIsActive(upload) {
  const phase = uploadStatusPhase(upload);
  return Boolean(upload && (upload.active || phase === "uploading" || phase === "processing"));
}

function uploadDestinationLabel(upload) {
  if (!uploadHasActivity(upload)) {
    return "Waiting for the next upload";
  }
  if (upload && upload.destination) {
    return upload.destination;
  }
  const root =
    (UPLOAD_ROOTS.find((entry) => entry.value === (upload && upload.section)) || UPLOAD_ROOTS[0]).root;
  return uploadDestinationPreview(root, upload && upload.folder);
}

function uploadFileCountLabel(upload) {
  const fileCount = Number((upload && upload.fileCount) || 0);
  const uploadedCount = Number((upload && upload.uploadedCount) || 0);
  if (!fileCount) {
    return "Waiting for file list";
  }
  if (uploadStatusPhase(upload) === "completed") {
    return `${uploadedCount || fileCount} of ${fileCount} saved`;
  }
  if (uploadedCount > 0) {
    return `${uploadedCount} of ${fileCount} saved`;
  }
  return `${fileCount} file${fileCount === 1 ? "" : "s"}`;
}

function uploadTransferredLabel(upload) {
  const bytesSent = Number((upload && upload.bytesSent) || 0);
  const bytesTotal = Number((upload && upload.bytesTotal) || 0);
  if (bytesTotal > 0) {
    return `${formatBytes(bytesSent)} / ${formatBytes(bytesTotal)}`;
  }
  if (bytesSent > 0) {
    return formatBytes(bytesSent);
  }
  return uploadHasActivity(upload) ? "Starting now" : "Waiting";
}

function uploadStatusCopy(upload) {
  const phase = uploadStatusPhase(upload);
  if (!uploadHasActivity(upload)) {
    return "";
  }
  if (phase === "error") {
    return upload.error || upload.message || "The last upload did not finish cleanly.";
  }
  if (upload.message) {
    return upload.message;
  }
  if (phase === "uploading") {
    return "Receiving files over Wi-Fi now.";
  }
  if (phase === "processing") {
    return "Transfer finished. The Pi is saving files and refreshing the library.";
  }
  if (phase === "completed") {
    return "Upload finished and the refreshed library is ready.";
  }
  return "Standing by for the next upload.";
}

function uploadCompletionRefreshKey(upload) {
  if (uploadStatusPhase(upload) !== "completed" || !upload || !upload.id) {
    return "";
  }
  return `${upload.id}|${upload.completedAt || upload.updatedAt || ""}`;
}

function metadataRefreshSnapshot() {
  return (state.status && state.status.metadataRefresh) || null;
}

function metadataRefreshPhase(refresh) {
  return String((refresh && refresh.phase) || "idle").toLowerCase();
}

function metadataRefreshHasActivity(refresh) {
  const phase = metadataRefreshPhase(refresh);
  return Boolean(
    refresh &&
      (refresh.active ||
        refresh.id ||
        refresh.startedAt ||
        phase === "skipped" ||
        phase === "completed" ||
        phase === "error" ||
        Number((refresh && refresh.outputLineCount) || 0) > 0),
  );
}

function metadataRefreshIsActive(refresh) {
  const phase = metadataRefreshPhase(refresh);
  return Boolean(refresh && (refresh.active || phase === "preparing" || phase === "running"));
}

function metadataRefreshPhaseLabel(refresh) {
  const phase = metadataRefreshPhase(refresh);
  if (phase === "preparing") return "Preparing";
  if (phase === "running") return "Running";
  if (phase === "completed") return "Complete";
  if (phase === "skipped") return "Skipped";
  if (phase === "error") return "Needs attention";
  return "Standing by";
}

function metadataRefreshPhaseTone(refresh) {
  const phase = metadataRefreshPhase(refresh);
  if (phase === "completed") return "success";
  if (phase === "error") return "error";
  if (phase === "preparing" || phase === "running") return "pending";
  return "";
}

function metadataRefreshCopy(refresh) {
  const phase = metadataRefreshPhase(refresh);
  if (!metadataRefreshHasActivity(refresh)) {
    return "Use Rescan Library to refresh posters, summaries, and ratings. Updates will show up here as they happen.";
  }
  if (phase === "error") {
    return refresh.error || refresh.message || "Metadata refresh stopped before it finished.";
  }
  if (phase === "skipped") {
    return refresh.message || "Metadata refresh was skipped this time.";
  }
  if (phase === "completed") {
    return refresh.message || "Metadata refresh finished.";
  }
  if (refresh && refresh.message) {
    return refresh.message;
  }
  if (phase === "preparing") {
    return "Getting everything ready before the library refresh starts.";
  }
  if (phase === "running") {
    return "Refreshing library details now. Live updates will keep appearing here.";
  }
  return "Ready whenever you want to refresh library details again.";
}

function metadataRefreshOutputLines(refresh) {
  if (!refresh) {
    return [];
  }
  const recentLines = Array.isArray(refresh.recentLines)
    ? refresh.recentLines.map((line) => String(line || "").trim()).filter(Boolean)
    : [];
  if (recentLines.length) {
    return recentLines;
  }
  return String(refresh.detail || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(-8);
}

function metadataRefreshConnectivityLabel(refresh) {
  if (!refresh) {
    return "Unavailable";
  }
  if (refresh.online) {
    return "Online";
  }
  if (metadataRefreshPhase(refresh) === "skipped" && refresh.reason === "offline") {
    return "Offline";
  }
  if (refresh.configured === false) {
    return "TMDb API key needed";
  }
  return "Waiting";
}

function renderMetadataRefreshActivity(target, refresh) {
  target.innerHTML = "";
  target.className = "upload-activity metadata-activity";

  const header = document.createElement("div");
  header.className = "upload-activity-header";

  const heading = document.createElement("p");
  heading.className = "upload-activity-title";
  heading.textContent = "Metadata Refresh Status";

  const badge = document.createElement("span");
  badge.className = "upload-phase-badge";
  const tone = metadataRefreshPhaseTone(refresh);
  if (tone) {
    badge.classList.add(`upload-phase-badge--${tone}`);
  }
  badge.textContent = metadataRefreshPhaseLabel(refresh);

  header.appendChild(heading);
  header.appendChild(badge);
  target.appendChild(header);

  const copy = document.createElement("p");
  copy.className = "upload-activity-copy";
  copy.textContent = metadataRefreshCopy(refresh);
  target.appendChild(copy);

  const metrics = document.createElement("div");
  metrics.className = "upload-activity-metrics";
  const outputCount = Number((refresh && refresh.outputLineCount) || 0);
  const rows = [
    { label: "Connectivity", value: metadataRefreshConnectivityLabel(refresh) },
    {
      label: "Output",
      value: outputCount ? `${outputCount} line${outputCount === 1 ? "" : "s"}` : "Waiting",
    },
    { label: "Started", value: formatTimestamp((refresh && refresh.startedAt) || "") || "Not started" },
    {
      label: "Updated",
      value: formatTimestamp((refresh && (refresh.completedAt || refresh.updatedAt)) || "") || "Waiting",
    },
  ];

  for (const row of rows) {
    const metric = document.createElement("div");
    metric.className = "upload-metric";
    const metricLabel = document.createElement("span");
    metricLabel.className = "upload-metric-label";
    metricLabel.textContent = row.label;
    const metricValue = document.createElement("strong");
    metricValue.className = "upload-metric-value";
    metricValue.textContent = row.value;
    metric.appendChild(metricLabel);
    metric.appendChild(metricValue);
    metrics.appendChild(metric);
  }
  target.appendChild(metrics);

  const lines = metadataRefreshOutputLines(refresh);
  if (lines.length) {
    const log = document.createElement("div");
    log.className = "metadata-activity-log";
    for (const line of lines) {
      const row = document.createElement("p");
      row.className = "metadata-activity-line";
      row.textContent = line;
      log.appendChild(row);
    }
    target.appendChild(log);
  }

  if (refresh && refresh.error && metadataRefreshPhase(refresh) !== "error") {
    const warning = document.createElement("p");
    warning.className = "upload-activity-warning";
    warning.textContent = refresh.error;
    target.appendChild(warning);
  }
}

function renderUploadActivity(target, upload) {
  target.innerHTML = "";
  target.className = "upload-activity";

  const header = document.createElement("div");
  header.className = "upload-activity-header";

  const heading = document.createElement("p");
  heading.className = "upload-activity-title";
  heading.textContent = "Shared Upload Status";

  const badge = document.createElement("span");
  badge.className = "upload-phase-badge";
  const tone = uploadPhaseTone(upload);
  if (tone) {
    badge.classList.add(`upload-phase-badge--${tone}`);
  }
  badge.textContent = uploadPhaseLabel(upload);

  header.appendChild(heading);
  header.appendChild(badge);
  target.appendChild(header);

  const copy = document.createElement("p");
  copy.className = "upload-activity-copy";
  copy.textContent = uploadStatusCopy(upload);
  target.appendChild(copy);

  const track = document.createElement("div");
  track.className = "upload-progress-track";
  const fill = document.createElement("div");
  fill.className = "upload-progress-fill";
  fill.style.width = `${uploadPercent(upload)}%`;
  track.appendChild(fill);
  target.appendChild(track);

  const progressLabel = document.createElement("p");
  progressLabel.className = "upload-progress-label";
  progressLabel.textContent = uploadHasActivity(upload)
    ? `${uploadPercent(upload)}% complete`
    : "Waiting for the next transfer";
  target.appendChild(progressLabel);

  const metrics = document.createElement("div");
  metrics.className = "upload-activity-metrics";
  const rows = [
    { label: "Destination", value: uploadDestinationLabel(upload) },
    { label: "Files", value: uploadFileCountLabel(upload) },
    { label: "Transferred", value: uploadTransferredLabel(upload) },
    {
      label: "Updated",
      value: formatTimestamp((upload && (upload.completedAt || upload.updatedAt)) || "") || "Waiting",
    },
  ];

  for (const row of rows) {
    const metric = document.createElement("div");
    metric.className = "upload-metric";
    const metricLabel = document.createElement("span");
    metricLabel.className = "upload-metric-label";
    metricLabel.textContent = row.label;
    const metricValue = document.createElement("strong");
    metricValue.className = "upload-metric-value";
    metricValue.textContent = row.value;
    metric.appendChild(metricLabel);
    metric.appendChild(metricValue);
    metrics.appendChild(metric);
  }
  target.appendChild(metrics);

  if (upload && Array.isArray(upload.warnings) && upload.warnings.length) {
    const warning = document.createElement("p");
    warning.className = "upload-activity-warning";
    warning.textContent = upload.warnings.join(" ");
    target.appendChild(warning);
  }
}

function pruneLiveUploadActivityTargets() {
  for (const target of Array.from(liveUploadActivityTargets)) {
    if (!target || !target.isConnected) {
      liveUploadActivityTargets.delete(target);
    }
  }
}

function registerLiveUploadActivityTarget(target) {
  pruneLiveUploadActivityTargets();
  if (target) {
    liveUploadActivityTargets.add(target);
  }
  return target;
}

function refreshLiveUploadActivity(upload = uploadStatusSnapshot()) {
  pruneLiveUploadActivityTargets();
  for (const target of liveUploadActivityTargets) {
    renderUploadActivity(target, upload);
  }
}

function pruneLiveMetadataActivityTargets() {
  for (const target of Array.from(liveMetadataActivityTargets)) {
    if (!target || !target.isConnected) {
      liveMetadataActivityTargets.delete(target);
    }
  }
}

function registerLiveMetadataActivityTarget(target) {
  pruneLiveMetadataActivityTargets();
  if (target) {
    liveMetadataActivityTargets.add(target);
  }
  return target;
}

function refreshLiveMetadataActivity(refresh = metadataRefreshSnapshot()) {
  pruneLiveMetadataActivityTargets();
  for (const target of liveMetadataActivityTargets) {
    renderMetadataRefreshActivity(target, refresh);
  }
}

function activeNetworkName(status) {
  return (status && (status.networkName || status.ssid)) || "";
}

function hotspotNetworkName(status) {
  return (status && (status.hotspotSsid || (status.networkMode === "hotspot" ? status.ssid : ""))) || appNetworkName();
}

function networkModeLabel(status) {
  const mode = String((status && status.networkMode) || "").toLowerCase();
  if (mode === "client") return "Joined known Wi-Fi";
  if (mode === "hotspot") return "Fallback hotspot active";
  if (mode === "offline") return "Offline";
  return "Status unavailable";
}

function deviceNetworkSubtitle(status, preferredUrl) {
  const mode = String((status && status.networkMode) || "").toLowerCase();
  const currentName = activeNetworkName(status);
  const hotspotName = hotspotNetworkName(status);

  if (mode === "client") {
    return `The Pi joined ${currentName || "a known Wi-Fi network"}. Clients on that network can open ${preferredUrl}.`;
  }

  if (mode === "hotspot") {
    return `No known Wi-Fi was available, so the Pi started ${hotspotName}. Join it and open ${preferredUrl}.`;
  }

  if (mode === "offline") {
    return status && status.fallbackApEnabled
      ? `The Pi is offline right now. It will try known Wi-Fi first and fall back to ${hotspotName} when needed.`
      : "The Pi is offline right now and fallback hotspot mode is disabled.";
  }

  return `Open ${preferredUrl} once the Pi joins a known network or starts ${hotspotName}.`;
}

function appBrandMark() {
  const initials = appDisplayName()
    .split(/[^A-Za-z0-9]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
  return initials || "MS";
}

function updateBranding() {
  if (els.brandTitle) {
    els.brandTitle.textContent = appDisplayName();
  }
  if (els.brandMark) {
    els.brandMark.textContent = appBrandMark();
  }
}

async function copyTextToClipboard(value) {
  if (!value) {
    return false;
  }

  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(value);
    return true;
  }

  const input = document.createElement("textarea");
  input.value = value;
  input.setAttribute("readonly", "readonly");
  input.style.position = "fixed";
  input.style.top = "-1000px";
  input.style.opacity = "0";
  document.body.appendChild(input);
  input.select();
  input.setSelectionRange(0, input.value.length);

  let copied = false;
  try {
    copied = document.execCommand("copy");
  } finally {
    document.body.removeChild(input);
  }

  return copied;
}

function compareText(left, right) {
  return String(left || "").localeCompare(String(right || ""), undefined, {
    numeric: true,
    sensitivity: "base",
  });
}

function seasonLabelForItem(item) {
  if (item.seasonLabel) {
    return item.seasonLabel;
  }
  if (item.seasonNumber === 0) {
    return "Specials";
  }
  if (item.seasonNumber > 0) {
    return `Season ${item.seasonNumber}`;
  }
  return "Season 1";
}

function seasonKeyForData(number, label) {
  return `${Number(number || 0)}|${String(label || "").toLowerCase()}`;
}

function seasonKeyForSeason(season) {
  if (!season) {
    return "";
  }
  return season.key || seasonKeyForData(season.number, season.label);
}

function seasonCodeLabel(season) {
  if (!season) {
    return "Season";
  }
  return season.number ? `S${String(season.number).padStart(2, "0")}` : "Specials";
}

function episodeCardTitle(item) {
  if (!item) {
    return "Episode";
  }

  const seasonPart = item.seasonNumber ? `S${String(item.seasonNumber).padStart(2, "0")}` : "";
  const episodePart = item.episodeNumber ? `E${String(item.episodeNumber).padStart(2, "0")}` : "";
  const code = `${seasonPart}${episodePart}`;
  return code ? `${code} ${item.title}` : item.title;
}

function normalizeLibraryItem(raw, versionToken) {
  const path = String(raw.path || "");
  const type = raw.type || mediaTypeForPath(path);
  const normalizedSection = raw.section === "photos" ? "documents" : raw.section;
  const section =
    !normalizedSection || normalizedSection === "library"
      ? sectionForPath(path, type)
      : normalizedSection;
  const title = raw.title || titleFromPath(path);
  const showTitle = raw.showTitle || "";
  const showSlug = raw.showSlug || (showTitle ? slugifyText(showTitle) : "");

  return {
    title,
    path,
    type,
    section,
    extension: String(raw.extension || extensionFromPath(path)).toUpperCase(),
    bytes: Number(raw.bytes || 0),
    streamUrl: path ? buildStreamUrl(path) : absoluteUrl(raw.streamUrl || ""),
    posterPath: raw.posterPath || "",
    backdropPath: raw.backdropPath || "",
    posterUrl: raw.posterUrl || buildAssetUrl(raw.posterPath, versionToken),
    backdropUrl: raw.backdropUrl || buildAssetUrl(raw.backdropPath, versionToken),
    sortTitle: raw.sortTitle || title,
    overview: raw.overview || "",
    tagline: raw.tagline || "",
    year: raw.year || "",
    releaseDate: raw.releaseDate || "",
    genres: raw.genres || "",
    contentRating: raw.contentRating || "",
    artist: raw.artist || "",
    album: raw.album || "",
    narrators: raw.narrators || "",
    publisher: raw.publisher || "",
    language: raw.language || "",
    tags: raw.tags || "",
    seriesName: raw.seriesName || "",
    seriesIndex: raw.seriesIndex ? String(raw.seriesIndex) : "",
    tmdbRating: Number(raw.tmdbRating || 0),
    runtimeMinutes: Number(raw.runtimeMinutes || 0),
    hasMetadata:
      raw.hasMetadata !== undefined
        ? Boolean(raw.hasMetadata)
        : Boolean(
            raw.source ||
              raw.overview ||
              raw.tagline ||
              raw.posterPath ||
              raw.backdropPath ||
              raw.genres ||
              raw.contentRating ||
              raw.tmdbRating ||
              raw.artist ||
              raw.album ||
              raw.narrators ||
              raw.publisher ||
              raw.language ||
              raw.tags ||
              raw.seriesName ||
              raw.seriesIndex ||
              raw.year,
          ),
    metadataSource: raw.metadataSource || raw.source || "",
    matchConfidence: Number(raw.matchConfidence || 0),
    showTitle,
    showSlug,
    seasonLabel: raw.seasonLabel || "",
    seasonNumber: Number(raw.seasonNumber || 0),
    episodeNumber: Number(raw.episodeNumber || 0),
  };
}

function createShowRecord(seed, versionToken) {
  const slug = seed.slug || slugifyText(seed.title || "unknown-show");
  return {
    title: seed.title || "Unknown Show",
    slug,
    year: seed.year || "",
    overview: seed.overview || "",
    genres: seed.genres || "",
    contentRating: seed.contentRating || "",
    posterPath: seed.posterPath || "",
    backdropPath: seed.backdropPath || "",
    posterUrl: seed.posterUrl || buildAssetUrl(seed.posterPath, versionToken),
    backdropUrl: seed.backdropUrl || buildAssetUrl(seed.backdropPath, versionToken),
    metadataSource: seed.metadataSource || seed.source || "",
    tmdbRating: Number(seed.tmdbRating || 0),
    matchConfidence: Number(seed.matchConfidence || 0),
    detailUrl: buildRoutePath({ name: "show", slug }),
    seasons: [],
    seasonCount: 0,
    episodeCount: 0,
    _seasonMap: new Map(),
  };
}

function buildLibraryFromIndex(raw) {
  const versionToken =
    (raw && raw.generatedAt) || (state.status && state.status.metadataGeneratedAt) || "";
  const sections = {
    movies: [],
    tv: [],
    music: [],
    audiobooks: [],
    documents: [],
  };
  const rawShows = new Map();

  for (const entry of Array.isArray(raw && raw.shows) ? raw.shows : []) {
    const show = createShowRecord(entry, versionToken);
    rawShows.set(show.slug, show);
  }

  const groupedShows = new Map();
  for (const entry of Array.isArray(raw && raw.items) ? raw.items : []) {
    const item = normalizeLibraryItem(entry, versionToken);

    if (item.section === "tv") {
      const slug = item.showSlug || slugifyText(item.showTitle || item.title);
      item.showSlug = slug;

      let show = groupedShows.get(slug);
      if (!show) {
        const seed =
          rawShows.get(slug) ||
          createShowRecord(
            {
              slug,
              title: item.showTitle || "Unknown Show",
              year: item.year,
              genres: item.genres,
              contentRating: item.contentRating,
              posterPath: item.posterPath,
              backdropPath: item.backdropPath,
              metadataSource: item.metadataSource,
              tmdbRating: item.tmdbRating,
              matchConfidence: item.matchConfidence,
            },
            versionToken,
          );
        show = createShowRecord(seed, versionToken);
        groupedShows.set(slug, show);
      }

      if (!show.title && item.showTitle) show.title = item.showTitle;
      if (!show.year && item.year) show.year = item.year;
      if (!show.overview && item.overview) show.overview = item.overview;
      if (!show.genres && item.genres) show.genres = item.genres;
      if (!show.contentRating && item.contentRating) show.contentRating = item.contentRating;
      if (!show.posterPath && item.posterPath) show.posterPath = item.posterPath;
      if (!show.backdropPath && item.backdropPath) show.backdropPath = item.backdropPath;
      if (!show.posterUrl && item.posterUrl) show.posterUrl = item.posterUrl;
      if (!show.backdropUrl && item.backdropUrl) show.backdropUrl = item.backdropUrl;
      if (!show.metadataSource && item.metadataSource) show.metadataSource = item.metadataSource;
      if (!show.tmdbRating && item.tmdbRating) show.tmdbRating = item.tmdbRating;
      if (!show.matchConfidence && item.matchConfidence) show.matchConfidence = item.matchConfidence;

      const seasonLabel = seasonLabelForItem(item);
      const seasonKey = seasonKeyForData(item.seasonNumber, seasonLabel);
      let season = show._seasonMap.get(seasonKey);
      if (!season) {
        season = {
          key: seasonKey,
          label: seasonLabel,
          number: Number(item.seasonNumber || 0),
          episodes: [],
        };
        show._seasonMap.set(seasonKey, season);
        show.seasons.push(season);
      }

      season.episodes.push(item);
      continue;
    }

    if (item.section === "movies") {
      sections.movies.push(item);
    } else if (item.section === "music") {
      sections.music.push(item);
    } else if (item.section === "audiobooks") {
      sections.audiobooks.push(item);
    } else if (item.section === "documents") {
      sections.documents.push(item);
    }
  }

  sections.tv = Array.from(groupedShows.values());

  for (const show of sections.tv) {
    for (const season of show.seasons) {
      season.episodes.sort((left, right) => {
        if (left.episodeNumber && right.episodeNumber && left.episodeNumber !== right.episodeNumber) {
          return left.episodeNumber - right.episodeNumber;
        }
        return compareText(left.sortTitle || left.title, right.sortTitle || right.title);
      });
      season.episodeCount = season.episodes.length;
    }

    show.seasons.sort((left, right) => {
      if (left.number !== right.number) {
        return left.number - right.number;
      }
      return compareText(left.label, right.label);
    });

    show.seasonCount = show.seasons.length;
    show.episodeCount = show.seasons.reduce(
      (total, season) => total + (season.episodes ? season.episodes.length : 0),
      0,
    );
    delete show._seasonMap;
  }

  sections.movies.sort((left, right) => compareText(left.sortTitle || left.title, right.sortTitle || right.title));
  sections.music.sort((left, right) => compareText(left.sortTitle || left.title, right.sortTitle || right.title));
  sections.audiobooks.sort((left, right) => compareText(left.sortTitle || left.title, right.sortTitle || right.title));
  sections.documents.sort((left, right) => compareText(left.sortTitle || left.title, right.sortTitle || right.title));
  sections.tv.sort((left, right) => compareText(left.title, right.title));

  const counts = {
    total:
      sections.movies.length +
      sections.tv.reduce((sum, show) => sum + show.episodeCount, 0) +
      sections.music.length +
      sections.audiobooks.length +
      sections.documents.length,
    movies: sections.movies.length,
    shows: sections.tv.length,
    episodes: sections.tv.reduce((sum, show) => sum + show.episodeCount, 0),
    music: sections.music.length,
    audiobooks: sections.audiobooks.length,
    documents: sections.documents.length,
  };

  return {
    count: counts.total,
    counts,
    metadata: {
      available: counts.total > 0 || rawShows.size > 0,
      generatedAt: (raw && raw.generatedAt) || "",
      generator: (raw && raw.generator) || "",
      itemCount: Array.isArray(raw && raw.items) ? raw.items.length : 0,
      showCount: Array.isArray(raw && raw.shows) ? raw.shows.length : 0,
    },
    sections,
  };
}

async function loadServerPlaybackState() {
  const localBackup = readLocalPlaybackBackup();

  let response;
  let payload;
  try {
    response = await fetch("/api/playback-state", { cache: "no-store" });
    payload = await response.json().catch(() => ({}));
  } catch (error) {
    if (hasLocalPlaybackBackup(localBackup)) {
      state.playbackProgress = localBackup.playback;
      state.watchedOverrides = localBackup.watched;
      state.playbackProfile = {
        source: "local-backup",
        sharedAcrossBrowsers: false,
      };
      state.playbackLoaded = true;
      return { ok: false, fallback: "local-backup" };
    }
    throw error;
  }

  if (!response.ok) {
    if (hasLocalPlaybackBackup(localBackup)) {
      state.playbackProgress = localBackup.playback;
      state.watchedOverrides = localBackup.watched;
      state.playbackProfile = {
        source: "local-backup",
        sharedAcrossBrowsers: false,
      };
      state.playbackLoaded = true;
      return { ok: false, fallback: "local-backup" };
    }
    throw new Error(payload.error || `Playback state returned HTTP ${response.status}`);
  }

  const serverPlayback = payload.playback && typeof payload.playback === "object" ? payload.playback : {};
  const serverWatched = payload.watched && typeof payload.watched === "object" ? payload.watched : {};
  const mergedPlayback = mergePlaybackMaps(serverPlayback, localBackup.playback);
  const mergedWatched = mergeWatchedMaps(serverWatched, localBackup.watched);

  state.playbackProgress = mergedPlayback;
  state.watchedOverrides = mergedWatched;
  state.playbackProfile = payload.profile || null;
  state.playbackLoaded = true;
  persistLocalPlaybackBackup();

  if (
    !playbackMapsEqual(serverPlayback, mergedPlayback) ||
    !watchedMapsEqual(serverWatched, mergedWatched)
  ) {
    enqueuePlaybackStateUpdate(
      {
        playback: Object.values(mergedPlayback),
        watched: mergedWatched,
      },
      { silent: true },
    );
  }

  return payload;
}

function serializePlaybackEntryForServer(entry) {
  if (!entry || !entry.path || !["movies", "tv", "audiobooks"].includes(entry.section)) {
    return null;
  }
  return {
    path: String(entry.path || ""),
    section: String(entry.section || ""),
    showSlug: String(entry.showSlug || ""),
    currentTime: Math.max(0, Number(entry.currentTime || 0)),
    duration: Math.max(0, Number(entry.duration || 0)),
    completed: Boolean(entry.completed),
    lastPlayedAt: String(entry.lastPlayedAt || ""),
  };
}

function normalizePlaybackEntrySnapshot(entry) {
  const safe = serializePlaybackEntryForServer(entry);
  return safe ? { ...safe } : null;
}

function readLocalPlaybackBackup() {
  const empty = { playback: {}, watched: {} };
  try {
    const raw = window.localStorage.getItem(PLAYBACK_BACKUP_KEY);
    if (!raw) {
      return empty;
    }
    const parsed = JSON.parse(raw);
    const playback = {};
    const watched = {};

    const playbackEntries =
      parsed && parsed.playback && typeof parsed.playback === "object"
        ? Array.isArray(parsed.playback)
          ? parsed.playback
          : Object.values(parsed.playback)
        : [];
    for (const entry of playbackEntries) {
      const safe = normalizePlaybackEntrySnapshot(entry);
      if (safe) {
        playback[safe.path] = safe;
      }
    }

    if (parsed && parsed.watched && typeof parsed.watched === "object") {
      for (const [path, value] of Object.entries(parsed.watched)) {
        const safePath = String(path || "").trim();
        if (!safePath) {
          continue;
        }
        watched[safePath] = Boolean(value);
      }
    }

    return { playback, watched };
  } catch (error) {
    return empty;
  }
}

function hasLocalPlaybackBackup(backup) {
  return Boolean(
    backup &&
      ((backup.playback && Object.keys(backup.playback).length) ||
        (backup.watched && Object.keys(backup.watched).length)),
  );
}

function playbackEntrySortTime(entry) {
  return Date.parse((entry && entry.lastPlayedAt) || 0) || 0;
}

function choosePlaybackEntry(left, right) {
  if (!left) {
    return right || null;
  }
  if (!right) {
    return left;
  }

  const leftTime = playbackEntrySortTime(left);
  const rightTime = playbackEntrySortTime(right);
  if (leftTime !== rightTime) {
    return leftTime >= rightTime ? left : right;
  }

  const leftProgress = playbackCurrentTime(left);
  const rightProgress = playbackCurrentTime(right);
  if (leftProgress !== rightProgress) {
    return leftProgress >= rightProgress ? left : right;
  }

  const leftDuration = playbackDuration(left);
  const rightDuration = playbackDuration(right);
  return leftDuration >= rightDuration ? left : right;
}

function mergePlaybackMaps(serverPlayback, localPlayback) {
  const merged = {};
  const keys = new Set([
    ...Object.keys(serverPlayback || {}),
    ...Object.keys(localPlayback || {}),
  ]);

  for (const path of keys) {
    const chosen = choosePlaybackEntry(
      normalizePlaybackEntrySnapshot(serverPlayback && serverPlayback[path]),
      normalizePlaybackEntrySnapshot(localPlayback && localPlayback[path]),
    );
    if (chosen) {
      merged[path] = chosen;
    }
  }

  return merged;
}

function mergeWatchedMaps(serverWatched, localWatched) {
  return {
    ...(serverWatched || {}),
    ...(localWatched || {}),
  };
}

function playbackMapsEqual(left, right) {
  const leftKeys = Object.keys(left || {}).sort();
  const rightKeys = Object.keys(right || {}).sort();
  if (leftKeys.length !== rightKeys.length) {
    return false;
  }

  for (let index = 0; index < leftKeys.length; index += 1) {
    if (leftKeys[index] !== rightKeys[index]) {
      return false;
    }
    const leftEntry = normalizePlaybackEntrySnapshot(left[leftKeys[index]]);
    const rightEntry = normalizePlaybackEntrySnapshot(right[rightKeys[index]]);
    if (!leftEntry || !rightEntry) {
      return false;
    }
    if (
      leftEntry.section !== rightEntry.section ||
      leftEntry.showSlug !== rightEntry.showSlug ||
      leftEntry.currentTime !== rightEntry.currentTime ||
      leftEntry.duration !== rightEntry.duration ||
      leftEntry.completed !== rightEntry.completed ||
      leftEntry.lastPlayedAt !== rightEntry.lastPlayedAt
    ) {
      return false;
    }
  }

  return true;
}

function watchedMapsEqual(left, right) {
  const leftKeys = Object.keys(left || {}).sort();
  const rightKeys = Object.keys(right || {}).sort();
  if (leftKeys.length !== rightKeys.length) {
    return false;
  }

  for (let index = 0; index < leftKeys.length; index += 1) {
    const key = leftKeys[index];
    if (key !== rightKeys[index] || Boolean(left[key]) !== Boolean(right[key])) {
      return false;
    }
  }

  return true;
}

function persistLocalPlaybackBackup() {
  try {
    const playback = {};
    for (const entry of Object.values(state.playbackProgress || {})) {
      const safe = normalizePlaybackEntrySnapshot(entry);
      if (safe) {
        playback[safe.path] = safe;
      }
    }

    const watched = {};
    for (const [path, value] of Object.entries(state.watchedOverrides || {})) {
      const safePath = String(path || "").trim();
      if (!safePath) {
        continue;
      }
      watched[safePath] = Boolean(value);
    }

    if (!Object.keys(playback).length && !Object.keys(watched).length) {
      window.localStorage.removeItem(PLAYBACK_BACKUP_KEY);
      return;
    }

    window.localStorage.setItem(
      PLAYBACK_BACKUP_KEY,
      JSON.stringify({
        savedAt: new Date().toISOString(),
        playback,
        watched,
      }),
    );
  } catch (error) {
    console.warn("Could not persist local playback backup", error);
  }
}

function postPlaybackStateUpdate(payload, options = {}) {
  const safePayload = {};
  if (Array.isArray(payload && payload.playback)) {
    const playback = payload.playback
      .map(serializePlaybackEntryForServer)
      .filter(Boolean);
    if (playback.length) {
      safePayload.playback = playback;
    }
  }
  if (payload && payload.watched && typeof payload.watched === "object") {
    const watched = {};
    for (const [path, value] of Object.entries(payload.watched)) {
      const safePath = String(path || "").trim();
      if (!safePath) {
        continue;
      }
      watched[safePath] = Boolean(value);
    }
    if (Object.keys(watched).length) {
      safePayload.watched = watched;
    }
  }
  if (Array.isArray(payload && payload.clearWatched)) {
    const clearWatched = payload.clearWatched
      .map((path) => String(path || "").trim())
      .filter(Boolean);
    if (clearWatched.length) {
      safePayload.clearWatched = clearWatched;
    }
  }
  if (!Object.keys(safePayload).length) {
    return Promise.resolve(null);
  }

  const body = JSON.stringify(safePayload);
  if (options.keepalive && navigator.sendBeacon) {
    try {
      const blob = new Blob([body], { type: "application/json" });
      if (navigator.sendBeacon("/api/playback-state", blob)) {
        return Promise.resolve(null);
      }
    } catch (error) {
      console.warn("Could not queue playback sync with sendBeacon", error);
    }
  }

  return fetch("/api/playback-state", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    cache: "no-store",
    keepalive: Boolean(options.keepalive),
    body,
  }).then(async (response) => {
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `Playback state returned HTTP ${response.status}`);
    }
    return data;
  });
}

function enqueuePlaybackStateUpdate(payload, options = {}) {
  if (options.keepalive) {
    return postPlaybackStateUpdate(payload, options).catch((error) => {
      console.warn("Could not sync playback state", error);
      if (!options.silent && els.pageSubtitle) {
        els.pageSubtitle.textContent = `Playback sync failed: ${error.message}`;
      }
      return null;
    });
  }

  playbackSyncQueue = playbackSyncQueue
    .catch(() => null)
    .then(() => postPlaybackStateUpdate(payload, options))
    .catch((error) => {
      console.warn("Could not sync playback state", error);
      if (!options.silent && els.pageSubtitle) {
        els.pageSubtitle.textContent = `Playback sync failed: ${error.message}`;
      }
      return null;
    });
  return playbackSyncQueue;
}

async function wipeServerPlaybackState() {
  const response = await fetch("/api/playback-state", {
    method: "DELETE",
    cache: "no-store",
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Playback wipe returned HTTP ${response.status}`);
  }
  return payload;
}

function clearNomadStorageBucket(storage, preservedKeys = []) {
  if (!storage) {
    return;
  }

  const keep = new Set(preservedKeys);
  for (let index = storage.length - 1; index >= 0; index -= 1) {
    const key = storage.key(index);
    if (!key || !key.startsWith(NOMAD_STORAGE_PREFIX) || keep.has(key)) {
      continue;
    }
    storage.removeItem(key);
  }
}

async function clearBrowserCaches() {
  if (!("caches" in window)) {
    return 0;
  }

  const cacheNames = await window.caches.keys();
  await Promise.all(cacheNames.map((cacheName) => window.caches.delete(cacheName)));
  return cacheNames.length;
}

function resetLocalState(keepWatchHistory) {
  clearSearch();
  state.pendingResume = null;
  state.lastProgressSaveAt = 0;
  state.playingItem = null;

  if (!keepWatchHistory) {
    state.playbackProgress = {};
    state.watchedOverrides = {};
    persistLocalPlaybackBackup();
  }

  resetPlayers();
  renderPlayerDetails(null);
}

function reloadAppWithFreshUrl() {
  const url = new URL(window.location.href);
  url.searchParams.set("refresh", String(Date.now()));
  window.location.replace(url.toString());
}

async function clearClientAppData(options = {}) {
  const keepWatchHistory = Boolean(options.keepWatchHistory);
  const confirmMessage = String(options.confirmMessage || "").trim();

  if (confirmMessage && !window.confirm(confirmMessage)) {
    return;
  }

  persistActivePlaybackProgress(true, false, false);
  els.pageSubtitle.textContent = keepWatchHistory
    ? "Clearing local app caches while keeping server watch history..."
    : "Wiping local app data and this device history profile...";

  try {
    await clearBrowserCaches();
  } catch (error) {
    console.warn("Could not clear Cache Storage", error);
  }

  try {
    clearNomadStorageBucket(window.localStorage);
  } catch (error) {
    console.warn("Could not clear localStorage data", error);
  }

  try {
    clearNomadStorageBucket(window.sessionStorage);
  } catch (error) {
    console.warn("Could not clear sessionStorage data", error);
  }

  if (!keepWatchHistory) {
    try {
      await wipeServerPlaybackState();
    } catch (error) {
      console.warn("Could not wipe server playback state", error);
      throw error;
    }
  }

  resetLocalState(keepWatchHistory);
  reloadAppWithFreshUrl();
}

function playbackEntryForPath(path) {
  if (!path) {
    return null;
  }
  return state.playbackProgress[path] || null;
}

function watchedOverrideForPath(path) {
  if (!path || !Object.prototype.hasOwnProperty.call(state.watchedOverrides, path)) {
    return null;
  }
  return Boolean(state.watchedOverrides[path]);
}

function playbackDuration(entry) {
  return Math.max(0, Number((entry && entry.duration) || 0));
}

function playbackCurrentTime(entry) {
  return Math.max(0, Number((entry && entry.currentTime) || 0));
}

function progressRatio(entry) {
  const duration = playbackDuration(entry);
  if (!duration) {
    return 0;
  }
  return Math.min(1, Math.max(0, playbackCurrentTime(entry) / duration));
}

function isPlaybackComplete(entry) {
  if (!entry) {
    return false;
  }

  if (entry.completed) {
    return true;
  }

  const duration = playbackDuration(entry);
  if (!duration) {
    return false;
  }

  const currentTime = playbackCurrentTime(entry);
  const remaining = Math.max(0, duration - currentTime);
  return progressRatio(entry) >= 0.92 || (currentTime >= 300 && remaining <= 90);
}

function hasResumeProgress(entry) {
  if (!entry) {
    return false;
  }

  if (playbackCurrentTime(entry) < RESUME_MIN_SECONDS) {
    return false;
  }

  return !isPlaybackComplete(entry);
}

function isTrackablePlaybackItem(item) {
  return Boolean(
    item &&
      item.path &&
      (
        (item.type === "video" && (item.section === "movies" || item.section === "tv")) ||
        (item.type === "audio" && item.section === "audiobooks")
      ),
  );
}

function isWatchStateItem(item) {
  return Boolean(
    item &&
      item.path &&
      (
        (item.type === "video" && (item.section === "movies" || item.section === "tv")) ||
        (item.type === "audio" && item.section === "audiobooks")
      ),
  );
}

function isWatchableVideoItem(item) {
  return Boolean(item && item.type === "video" && item.path && (item.section === "movies" || item.section === "tv"));
}

function isItemWatched(item) {
  if (!isWatchStateItem(item)) {
    return false;
  }

  const override = watchedOverrideForPath(item.path);
  if (override !== null) {
    return override;
  }

  return isPlaybackComplete(playbackEntryForPath(item.path));
}

function seasonWatchItems(season) {
  return (Array.isArray(season && season.episodes) ? season.episodes : []).filter(isWatchableVideoItem);
}

function showWatchItems(show) {
  const items = [];
  for (const season of show && show.seasons ? show.seasons : []) {
    items.push(...seasonWatchItems(season));
  }
  return items;
}

function isSeasonWatched(season) {
  const items = seasonWatchItems(season);
  return items.length > 0 && items.every((item) => isItemWatched(item));
}

function isShowWatched(show) {
  const items = showWatchItems(show);
  return items.length > 0 && items.every((item) => isItemWatched(item));
}

function hasResumeProgressForItem(item) {
  if (!isTrackablePlaybackItem(item)) {
    return false;
  }

  if (item.type === "video" && isItemWatched(item)) {
    return false;
  }
  return hasResumeProgress(playbackEntryForPath(item.path));
}

function primaryActionLabelForItem(item) {
  if (item.type === "image") {
    return "Open Preview";
  }
  return hasResumeProgressForItem(item) ? "Resume" : "Play Now";
}

function resumeEntryForShow(show) {
  const detailedShow = findShow(show && show.slug);
  if (!detailedShow || !Array.isArray(detailedShow.seasons)) {
    return null;
  }

  let latest = null;
  for (const season of detailedShow.seasons || []) {
    for (const episode of season.episodes || []) {
      if (!hasResumeProgressForItem(episode)) {
        continue;
      }
      const entry = playbackEntryForPath(episode.path);
      if (!entry) {
        continue;
      }
      const sortTime = Date.parse(entry.lastPlayedAt || 0) || 0;
      if (!latest || sortTime > latest.sortTime) {
        latest = { item: episode, entry, sortTime };
      }
    }
  }

  return latest;
}

function resumeProgressForItem(item) {
  if (!hasResumeProgressForItem(item)) {
    return null;
  }

  const entry = playbackProgressForItem(item);
  if (!entry) {
    return null;
  }

  return {
    entry,
    percent: progressPercent(entry),
    label: progressLabel(entry),
  };
}

function completionDurationForItem(item, previousEntry) {
  const previousDuration = playbackDuration(previousEntry);
  if (previousDuration > 0) {
    return previousDuration;
  }

  const runtimeSeconds = Math.round(Number(item && item.runtimeMinutes) * 60);
  if (Number.isFinite(runtimeSeconds) && runtimeSeconds > 0) {
    return runtimeSeconds;
  }

  return 1;
}

function markPlaybackEntryWatched(item, timestamp) {
  const previous = playbackEntryForPath(item.path) || {};
  const duration = completionDurationForItem(item, previous);

  state.playbackProgress[item.path] = {
    ...previous,
    path: item.path,
    section: item.section,
    showSlug: item.showSlug || previous.showSlug || "",
    currentTime: duration,
    duration,
    completed: true,
    lastPlayedAt: timestamp || new Date().toISOString(),
  };
}

function setWatchedStateForItems(items, watched) {
  const targets = items.filter(isWatchStateItem);
  if (!targets.length) {
    return;
  }

  const playbackUpdates = [];
  const watchedUpdates = {};
  if (watched) {
    const baseTime = Date.now();
    for (let index = 0; index < targets.length; index += 1) {
      const item = targets[index];
      markPlaybackEntryWatched(item, new Date(baseTime + index).toISOString());
      state.watchedOverrides[item.path] = true;
      watchedUpdates[item.path] = true;
      playbackUpdates.push(state.playbackProgress[item.path]);
    }
  } else {
    for (const item of targets) {
      state.watchedOverrides[item.path] = false;
      watchedUpdates[item.path] = false;
    }
  }

  enqueuePlaybackStateUpdate(
    {
      playback: playbackUpdates,
      watched: watchedUpdates,
    },
    { silent: false },
  );
  persistLocalPlaybackBackup();
  render();
}

function movieOrEpisodeWatchState(item) {
  if (!isWatchStateItem(item)) {
    return null;
  }

  const watched = isItemWatched(item);
  const noun = item.section === "movies" ? "movie" : item.section === "audiobooks" ? "audiobook" : "episode";
  const verb = item.section === "audiobooks" ? "played" : "watched";
  return {
    watched,
    label: watched ? `Mark ${noun} un${verb}` : `Mark ${noun} ${verb}`,
    onToggle: () => setWatchedStateForItems([item], !watched),
  };
}

function seasonWatchState(season) {
  const items = seasonWatchItems(season);
  if (!items.length) {
    return null;
  }

  const watched = isSeasonWatched(season);
  return {
    watched,
    label: watched ? `Mark ${season.label || "season"} unwatched` : `Mark ${season.label || "season"} watched`,
    onToggle: () => setWatchedStateForItems(items, !watched),
  };
}

function showWatchState(show) {
  const items = showWatchItems(show);
  if (!items.length) {
    return null;
  }

  const watched = isShowWatched(show);
  return {
    watched,
    label: watched ? `Mark ${show.title || "show"} unwatched` : `Mark ${show.title || "show"} watched`,
    onToggle: () => setWatchedStateForItems(items, !watched),
  };
}

function formatPlaybackTime(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds || 0)));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainder = total % 60;

  if (hours) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
  }

  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function progressPercent(entry) {
  return Math.round(progressRatio(entry) * 100);
}

function progressLabel(entry) {
  const duration = playbackDuration(entry);
  const currentTime = playbackCurrentTime(entry);
  if (!duration) {
    return `Resume from ${formatPlaybackTime(currentTime)}`;
  }

  return `${progressPercent(entry)}% watched | ${formatPlaybackTime(currentTime)} / ${formatPlaybackTime(duration)}`;
}

function nextEpisodeForShow(show, path) {
  const episodes = [];
  for (const season of show.seasons || []) {
    for (const episode of season.episodes || []) {
      episodes.push(episode);
    }
  }

  const index = episodes.findIndex((episode) => episode.path === path);
  if (index < 0) {
    return null;
  }

  return episodes[index + 1] || null;
}

function continueWatchingEntries() {
  const shows = detailedCatalogShows();
  const resumes = [];
  const nextUps = [];
  const resumePaths = new Set();

  for (const item of allMediaItems()) {
    if (!isTrackablePlaybackItem(item)) {
      continue;
    }

    const entry = playbackEntryForPath(item.path);
    if (!hasResumeProgressForItem(item)) {
      continue;
    }

    resumePaths.add(item.path);
    resumes.push({
      kind: "resume",
      item,
      entry,
      sortTime: Date.parse(entry.lastPlayedAt || 0) || 0,
    });
  }

  resumes.sort((left, right) => right.sortTime - left.sortTime);

  for (const show of shows) {
    let latest = null;

    for (const season of show.seasons || []) {
      for (const episode of season.episodes || []) {
        const entry = playbackEntryForPath(episode.path);
        if (!entry) {
          continue;
        }

        const sortTime = Date.parse(entry.lastPlayedAt || 0) || 0;
        if (!latest || sortTime > latest.sortTime) {
          latest = { item: episode, entry, sortTime };
        }
      }
    }

    if (!latest || hasResumeProgressForItem(latest.item) || !isItemWatched(latest.item)) {
      continue;
    }

    let nextEpisode = nextEpisodeForShow(show, latest.item.path);
    while (
      nextEpisode &&
      (resumePaths.has(nextEpisode.path) || hasResumeProgressForItem(nextEpisode) || isItemWatched(nextEpisode))
    ) {
      nextEpisode = nextEpisodeForShow(show, nextEpisode.path);
    }

    if (!nextEpisode) {
      continue;
    }

    nextUps.push({
      kind: "next",
      item: nextEpisode,
      show,
      fromItem: latest.item,
      sortTime: latest.sortTime,
    });
  }

  nextUps.sort((left, right) => right.sortTime - left.sortTime);
  return resumes.concat(nextUps).slice(0, 8);
}

function playbackProgressForItem(item) {
  return playbackEntryForPath(item && item.path);
}

function resumeTimeForItem(item) {
  const entry = playbackProgressForItem(item);
  if (!hasResumeProgressForItem(item)) {
    return 0;
  }

  const duration = playbackDuration(entry);
  if (!duration) {
    return playbackCurrentTime(entry);
  }

  return Math.max(0, Math.min(playbackCurrentTime(entry), duration - 3));
}

function activePlaybackElement() {
  if (!state.playingItem) {
    return null;
  }

  if (state.playingItem.type === "video") {
    return els.video;
  }

  if (state.playingItem.type === "audio") {
    return els.audio;
  }

  return null;
}

function recordPlaybackProgress(item, currentTime, duration, options = {}) {
  if (!isTrackablePlaybackItem(item) || !Number.isFinite(currentTime) || currentTime < 0) {
    return;
  }

  const previous = playbackEntryForPath(item.path) || {};
  const safeDuration =
    Number.isFinite(duration) && duration > 0 ? Number(duration) : playbackDuration(previous);
  const entry = {
    path: item.path,
    section: item.section,
    showSlug: item.showSlug || "",
    currentTime: Math.max(0, Number(currentTime)),
    duration: safeDuration,
    completed: false,
    lastPlayedAt: new Date().toISOString(),
  };

  entry.completed = Boolean(options.completed) || isPlaybackComplete(entry);
  if (entry.completed && entry.duration > 0) {
    entry.currentTime = entry.duration;
  }

  state.playbackProgress[item.path] = entry;
  const clearWatched = [];

  if (entry.completed && watchedOverrideForPath(item.path) === false) {
    delete state.watchedOverrides[item.path];
    clearWatched.push(item.path);
  }

  persistLocalPlaybackBackup();

  enqueuePlaybackStateUpdate(
    {
      playback: [entry],
      clearWatched,
    },
    {
      keepalive: Boolean(options.keepalive),
      silent: true,
    },
  );
}

function persistActivePlaybackProgress(force, completed, refreshHome, keepalive = false) {
  const item = state.playingItem;
  const element = activePlaybackElement();
  if (!isTrackablePlaybackItem(item) || !element || !element.currentSrc) {
    return;
  }

  const now = Date.now();
  if (!force && now - state.lastProgressSaveAt < PROGRESS_SAVE_INTERVAL_MS) {
    return;
  }

  if (!Number.isFinite(element.currentTime)) {
    return;
  }

  state.lastProgressSaveAt = now;
  recordPlaybackProgress(item, element.currentTime, element.duration, { completed, keepalive });

  if (refreshHome && state.route.name === "home" && !state.query) {
    render();
  }
}

function applyPendingPlaybackResume() {
  if (
    !state.pendingResume ||
    !state.playingItem ||
    !isTrackablePlaybackItem(state.playingItem) ||
    state.pendingResume.path !== state.playingItem.path
  ) {
    return;
  }

  const element = activePlaybackElement();
  if (!element) {
    return;
  }

  const target = Number(state.pendingResume.time || 0);
  if (!(target > 0)) {
    state.pendingResume = null;
    return;
  }

  let safeTarget = target;
  if (Number.isFinite(element.duration) && element.duration > 0) {
    safeTarget = Math.max(0, Math.min(target, element.duration - 3));
  }

  if (safeTarget > 0 && Math.abs(element.currentTime - safeTarget) > 1) {
    try {
      element.currentTime = safeTarget;
    } catch (error) {
      return;
    }
  }

  els.playerCaption.textContent = `${state.playingItem.title} | resuming at ${formatPlaybackTime(safeTarget)}`;
  state.pendingResume = null;
}

function uniqueBits(bits) {
  const seen = new Set();

  return bits.filter((bit) => {
    const value = String(bit || "").trim();
    if (!value) {
      return false;
    }

    const key = value.toLowerCase();
    if (seen.has(key)) {
      return false;
    }

    seen.add(key);
    return true;
  });
}

function joinBits(bits) {
  return uniqueBits(bits).join(" | ");
}

function truncateText(value, maxLength) {
  const text = String(value || "").trim();
  if (!text || text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength - 3).trim()}...`;
}

function formatBytes(bytes) {
  if (!bytes) {
    return "0 KB";
  }
  if (bytes < 1024 * 1024) {
    return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  }
  if (bytes < 1024 * 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function formatRuntime(minutes) {
  const value = Number(minutes || 0);
  if (!value) {
    return "";
  }

  const rounded = Math.round(value);
  if (rounded < 60) {
    return `${rounded} min`;
  }

  const hours = Math.floor(rounded / 60);
  const remainder = rounded % 60;
  if (!remainder) {
    return `${hours}h`;
  }

  return `${hours}h ${remainder}m`;
}

function formatTimestamp(value) {
  if (!value) {
    return "";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return date.toLocaleString();
}

function formatDate(value) {
  if (!value) {
    return "";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function titleWithYear(title, year) {
  const safeTitle = String(title || "").trim();
  const safeYear = String(year || "").trim();
  if (!safeYear || !safeTitle || safeTitle.includes(safeYear)) {
    return safeTitle;
  }
  return `${safeTitle} (${safeYear})`;
}

function formatConfidence(value) {
  const number = Number(value || 0);
  if (!(number > 0)) {
    return "";
  }
  return `${Math.round(number * 100)}%`;
}

function formatTmdbRating(value) {
  const number = Number(value || 0);
  if (!(number > 0)) {
    return "";
  }
  return `${number.toFixed(1)}/10`;
}

function createInfoChip(labelText, value, options = {}) {
  const safeValue = String(value || "").trim();
  if (!safeValue) {
    return null;
  }

  const chip = document.createElement(options.tagName || "span");
  chip.className = ["rating-chip", options.className].filter(Boolean).join(" ");

  const label = document.createElement("span");
  label.className = "rating-chip-label";
  label.textContent = String(labelText || "").trim();

  const score = document.createElement("strong");
  score.className = "rating-chip-value";
  score.textContent = safeValue;

  chip.appendChild(label);
  chip.appendChild(score);
  chip.setAttribute("aria-label", options.ariaLabel || [String(labelText || "").trim(), safeValue].filter(Boolean).join(" "));
  return chip;
}

function createRatingChip(value, options = {}) {
  const rating = formatTmdbRating(value);
  if (!rating) {
    return null;
  }

  return createInfoChip(options.label || "TMDb", rating, {
    ...options,
    ariaLabel: `${options.label || "TMDb"} rating ${rating}`,
  });
}

function createCardArtIcon(icon) {
  const namespace = "http://www.w3.org/2000/svg";
  const wrap = document.createElement("div");
  wrap.className = `card-art-icon card-art-icon--${icon}`;
  wrap.setAttribute("aria-hidden", "true");

  const svg = document.createElementNS(namespace, "svg");
  svg.setAttribute("viewBox", "0 0 64 64");
  svg.setAttribute("focusable", "false");

  if (icon === "folder") {
    const back = document.createElementNS(namespace, "path");
    back.setAttribute(
      "d",
      "M10 18c0-3.3 2.7-6 6-6h10.5c1.6 0 3.2.6 4.3 1.8l3.4 3.2H48c3.3 0 6 2.7 6 6v2H10v-7z",
    );
    back.setAttribute("class", "card-art-icon-shape card-art-icon-shape--folder-back");
    svg.appendChild(back);

    const front = document.createElementNS(namespace, "path");
    front.setAttribute(
      "d",
      "M8 26c0-3.3 2.7-6 6-6h36c3.3 0 6 2.7 6 6v18c0 4.4-3.6 8-8 8H16c-4.4 0-8-3.6-8-8V26z",
    );
    front.setAttribute("class", "card-art-icon-shape card-art-icon-shape--folder-front");
    svg.appendChild(front);
  } else if (icon === "pdf") {
    const page = document.createElementNS(namespace, "path");
    page.setAttribute(
      "d",
      "M18 8h19l11 11v31c0 3.3-2.7 6-6 6H18c-3.3 0-6-2.7-6-6V14c0-3.3 2.7-6 6-6z",
    );
    page.setAttribute("class", "card-art-icon-shape card-art-icon-shape--pdf-page");
    svg.appendChild(page);

    const fold = document.createElementNS(namespace, "path");
    fold.setAttribute("d", "M37 8v11h11");
    fold.setAttribute("class", "card-art-icon-line card-art-icon-line--pdf-fold");
    svg.appendChild(fold);

    const label = document.createElementNS(namespace, "path");
    label.setAttribute(
      "d",
      "M17 39c0-2.2 1.8-4 4-4h22c2.2 0 4 1.8 4 4v7c0 2.2-1.8 4-4 4H21c-2.2 0-4-1.8-4-4v-7z",
    );
    label.setAttribute("class", "card-art-icon-shape card-art-icon-shape--pdf-label");
    svg.appendChild(label);

    const text = document.createElementNS(namespace, "text");
    text.setAttribute("x", "32");
    text.setAttribute("y", "45");
    text.setAttribute("text-anchor", "middle");
    text.setAttribute("class", "card-art-icon-text");
    text.textContent = "PDF";
    svg.appendChild(text);
  }

  wrap.appendChild(svg);
  return wrap;
}

function countLabel(count, singular, plural) {
  return `${count} ${count === 1 ? singular : plural || `${singular}s`}`;
}

function homeSummaryText(summary) {
  return [
    countLabel(summary.movies, "movie"),
    countLabel(summary.shows, "show"),
    countLabel(summary.episodes, "episode"),
    countLabel(summary.music, "song"),
    countLabel(summary.audiobooks, "audiobook"),
    countLabel(summary.documents, "document"),
  ].join(" | ");
}

function matchesQuery(text) {
  return !state.query || text.toLowerCase().includes(state.query.toLowerCase());
}

function routeGenreSection() {
  if (state.route.name === "movies") {
    return "movies";
  }
  if (state.route.name === "tv") {
    return "tv";
  }
  if (isAudiobookBrowseRoute()) {
    return "audiobooks";
  }
  return "";
}

function currentGenreFilter(section = routeGenreSection()) {
  if (!section) {
    return "";
  }
  return String((state.genreFilters && state.genreFilters[section]) || "").trim();
}

function splitGenreList(value) {
  const known = new Set();
  const genres = [];
  for (const rawPart of String(value || "").split(",")) {
    const genre = rawPart.trim();
    if (!genre) {
      continue;
    }
    const key = genre.toLowerCase();
    if (known.has(key)) {
      continue;
    }
    known.add(key);
    genres.push(genre);
  }
  return genres;
}

function matchesGenreFilter(rawGenres, section = routeGenreSection()) {
  const activeGenre = currentGenreFilter(section);
  if (!activeGenre) {
    return true;
  }
  const selected = activeGenre.toLowerCase();
  return splitGenreList(rawGenres).some((genre) => genre.toLowerCase() === selected);
}

function genreFilterLabel(section = routeGenreSection()) {
  return currentGenreFilter(section);
}

function routeUsesCatalogSummary() {
  return [
    "home",
    "movies",
    "movie",
    "tv",
    "show",
    "season",
    "audiobooks",
    "audiobookCollection",
    "audiobookAuthor",
    "audiobook",
  ].includes(state.route.name);
}

function routeUsesServerSearch() {
  return ["home", "movies", "tv", "audiobooks", "audiobookCollection", "audiobookAuthor"].includes(state.route.name);
}

function homeSections() {
  if (state.catalogHome && state.catalogHome.sections) {
    return {
      movies: state.catalogHome.sections.movies || [],
      tv: state.catalogHome.sections.tv || [],
      music: state.catalogHome.sections.music || [],
      audiobooks: state.catalogHome.sections.audiobooks || [],
      documents: state.catalogHome.sections.documents || [],
    };
  }
  return {
    movies: [],
    tv: [],
    music: [],
    audiobooks: [],
    documents: [],
  };
}

function detailedCatalogShows() {
  const known = new Map();
  const addShow = (show) => {
    if (!show || !show.slug || known.has(show.slug) || !Array.isArray(show.seasons)) {
      return;
    }
    known.set(show.slug, show);
  };

  addShow(state.catalogShow);
  for (const show of (state.catalogProgress && state.catalogProgress.shows) || []) {
    addShow(show);
  }
  for (const show of librarySections().tv || []) {
    addShow(show);
  }
  return Array.from(known.values());
}

function librarySections() {
  if (!state.library || !state.library.sections) {
    return {
      movies: [],
      tv: [],
      music: [],
      audiobooks: [],
      documents: [],
    };
  }

  return {
    movies: state.library.sections.movies || [],
    tv: state.library.sections.tv || [],
    music: state.library.sections.music || [],
    audiobooks: state.library.sections.audiobooks || [],
    documents: state.library.sections.documents || [],
  };
}

function allMediaItems() {
  const known = new Map();
  const addItem = (item) => {
    if (!item || !item.path || known.has(item.path)) {
      return;
    }
    known.set(item.path, item);
  };
  const addShowEpisodes = (show) => {
    for (const season of (show && show.seasons) || []) {
      for (const episode of season.episodes || []) {
        addItem(episode);
      }
    }
  };

  if (state.catalogMovie) {
    addItem(state.catalogMovie);
  }
  if (state.catalogAudiobook) {
    addItem(state.catalogAudiobook);
  }
  for (const item of (state.catalogProgress && state.catalogProgress.items) || []) {
    addItem(item);
  }
  for (const item of state.catalogMovies.items || []) {
    addItem(item);
  }
  for (const item of state.catalogAudiobooks.items || []) {
    addItem(item);
  }
  for (const item of state.catalogSearch.items || []) {
    addItem(item);
  }
  const home = homeSections();
  for (const item of home.movies || []) {
    addItem(item);
  }
  for (const item of home.music || []) {
    addItem(item);
  }
  for (const item of home.audiobooks || []) {
    addItem(item);
  }
  for (const item of home.documents || []) {
    addItem(item);
  }
  addShowEpisodes(state.catalogShow);
  for (const show of (state.catalogProgress && state.catalogProgress.shows) || []) {
    addShowEpisodes(show);
  }

  const sections = librarySections();
  for (const item of sections.movies || []) {
    addItem(item);
  }
  for (const item of sections.music || []) {
    addItem(item);
  }
  for (const item of sections.audiobooks || []) {
    addItem(item);
  }
  for (const item of sections.documents || []) {
    addItem(item);
  }
  for (const show of sections.tv || []) {
    addShowEpisodes(show);
  }

  return Array.from(known.values());
}

function findShow(slug) {
  if (state.catalogShow && state.catalogShow.slug === slug) {
    return state.catalogShow;
  }
  const progressShow = ((state.catalogProgress && state.catalogProgress.shows) || []).find((show) => show.slug === slug);
  if (progressShow) {
    return progressShow;
  }
  const pagedShow = (state.catalogShows.items || []).find((show) => show.slug === slug);
  if (pagedShow) {
    return pagedShow;
  }
  const homeShow = (homeSections().tv || []).find((show) => show.slug === slug);
  if (homeShow) {
    return homeShow;
  }
  const sections = librarySections();
  return sections.tv.find((show) => show.slug === slug) || null;
}

function findMediaItemByPath(path) {
  return allMediaItems().find((item) => item.path === path) || null;
}

function findSeason(show, seasonKey) {
  if (!show || !seasonKey) {
    return null;
  }
  return (show.seasons || []).find((season) => seasonKeyForSeason(season) === seasonKey) || null;
}

function firstSeason(show) {
  return show && show.seasons && show.seasons.length ? show.seasons[0] : null;
}

function firstEpisodeInSeason(season) {
  return season && season.episodes && season.episodes.length ? season.episodes[0] : null;
}

function playbackCatalogPaths() {
  const known = new Set();
  const paths = [];
  const addPath = (value) => {
    const path = String(value || "").trim();
    if (!path || known.has(path)) {
      return;
    }
    known.add(path);
    paths.push(path);
  };

  for (const path of Object.keys(state.playbackProgress || {})) {
    addPath(path);
  }
  for (const [path, watched] of Object.entries(state.watchedOverrides || {})) {
    if (watched) {
      addPath(path);
    }
  }
  if (state.playingItem && state.playingItem.path) {
    addPath(state.playingItem.path);
  }
  return paths;
}

function seasonRoute(show, season) {
  if (!show || !season) {
    return { name: "tv" };
  }
  return {
    name: "season",
    slug: show.slug,
    seasonKey: seasonKeyForSeason(season),
  };
}

function searchableMediaText(item) {
  return [
    item.title,
    item.sortTitle,
    item.extension,
    item.section,
    item.showTitle,
    item.seasonLabel,
    item.overview,
    item.tagline,
    item.year,
    item.releaseDate,
    item.genres,
    item.contentRating,
    item.artist,
    item.album,
    item.metadataSource,
    item.path,
  ]
    .filter(Boolean)
    .join(" ");
}

function searchableShowText(show) {
  const seasonLabels = (show.seasons || []).map((season) => season.label).join(" ");
  return [
    show.title,
    show.year,
    show.overview,
    show.genres,
    show.contentRating,
    seasonLabels,
  ]
    .filter(Boolean)
    .join(" ");
}

function filterMediaItems(items) {
  return items.filter((item) => {
    return matchesQuery(searchableMediaText(item)) && matchesGenreFilter(item && item.genres, routeGenreSection());
  });
}

function filterShows(shows) {
  return shows.filter((show) => matchesQuery(searchableShowText(show)) && matchesGenreFilter(show && show.genres, "tv"));
}

function firstEpisode(show) {
  if (!show) {
    return null;
  }

  for (const season of show.seasons || []) {
    if (season.episodes && season.episodes.length) {
      return season.episodes[0];
    }
  }

  return null;
}

function hashString(value) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) % 360;
  }
  return hash;
}

function coverGradient(key, variant) {
  const hue = hashString(key || "nomad-screen");
  const hue2 = (hue + 56) % 360;
  const portrait = variant === "portrait";
  const angle = portrait ? "180deg" : "145deg";
  const baseA = portrait ? 58 : 54;
  const baseB = portrait ? 16 : 14;

  return [
    `linear-gradient(140deg, hsla(${hue}, 88%, 62%, 0.34), transparent 40%)`,
    `linear-gradient(220deg, hsla(${hue2}, 82%, 58%, 0.26), transparent 58%)`,
    `linear-gradient(${angle}, hsl(${(hue + 8) % 360}, ${baseA}%, 14%), hsl(${(hue2 + 10) % 360}, 46%, ${baseB}%))`,
  ].join(", ");
}

function createArtBackground(imageUrl, fallbackKey, variant) {
  const gradient = coverGradient(fallbackKey, variant);
  if (!imageUrl) {
    return gradient;
  }

  return [
    "linear-gradient(180deg, rgba(4, 10, 18, 0.18), rgba(4, 10, 18, 0.52))",
    gradient,
    `url("${imageUrl}") center/cover no-repeat`,
  ].join(", ");
}

function createFactPill(label) {
  const pill = document.createElement("span");
  pill.className = "fact-pill";
  pill.textContent = label;
  return pill;
}

function normalizeAudiobookCollectionLabel(value) {
  const raw = String(value || "").trim();
  if (!raw) {
    return "";
  }

  const cleaned = raw.replace(/\s+#\d+(?:\.\d+)?\s*$/i, "").trim();
  return cleaned || raw;
}

function audiobookEmbeddedSeriesIndex(value) {
  const match = String(value || "").trim().match(/\s+#(\d+(?:\.\d+)?)\s*$/i);
  return match ? String(match[1] || "").trim() : "";
}

function audiobookSeriesName(item) {
  return normalizeAudiobookCollectionLabel(item && item.seriesName);
}

function audiobookSeriesIndexValue(item) {
  const embeddedValue =
    audiobookEmbeddedSeriesIndex(item && item.seriesName) ||
    audiobookEmbeddedSeriesIndex(item && item.album);
  if (embeddedValue) {
    return String(embeddedValue || "").trim();
  }

  const directValue = String((item && item.seriesIndex) || "").trim();
  return directValue;
}

function audiobookBookNumberLabel(item) {
  const seriesIndex = audiobookSeriesIndexValue(item);
  return seriesIndex ? `Book ${seriesIndex}` : "";
}

function audiobookSeriesLabel(item) {
  if (!item) {
    return "";
  }

  const seriesName = audiobookSeriesName(item);
  const seriesIndex = audiobookSeriesIndexValue(item);
  if (seriesName && seriesIndex) {
    return `${seriesName} - Book ${seriesIndex}`;
  }
  if (seriesName) {
    return seriesName;
  }
  if (seriesIndex) {
    return `Book ${seriesIndex}`;
  }
  return "";
}

function audiobookNarratorLabel(item) {
  return item && item.narrators ? `Narrated by ${item.narrators}` : "";
}

function itemDetailFacts(item, probe) {
  const facts = [];
  const audiobookCollection = item && item.section === "audiobooks" ? audiobookCollectionName(item) : "";
  const audiobookSeries = item && item.section === "audiobooks" ? audiobookSeriesLabel(item) : "";

  if (item.section === "tv" && item.showTitle) {
    facts.push(item.showTitle);
  }
  if (item.seasonLabel) {
    facts.push(item.seasonLabel);
  }
  if (item.episodeNumber) {
    facts.push(`Episode ${item.episodeNumber}`);
  }
  if (item.artist) {
    facts.push(item.artist);
  }
  if (item.album && item.section !== "audiobooks") {
    facts.push(item.album);
  }
  if (item.section === "audiobooks") {
    if (audiobookCollection && audiobookCollection !== item.artist && audiobookCollection !== audiobookSeries) {
      facts.push(audiobookCollection);
    }
    facts.push("Audiobook");
    if (item.narrators) {
      facts.push(audiobookNarratorLabel(item));
    }
    if (item.publisher) {
      facts.push(item.publisher);
    }
    if (item.language) {
      facts.push(item.language);
    }
    const seriesLabel = audiobookSeries;
    if (seriesLabel) {
      facts.push(seriesLabel);
    }
  }
  if (item.section === "documents") {
    facts.push(item.type === "image" ? "Image file" : "Document");
  }
  if (item.year) {
    facts.push(item.year);
  }
  if (item.contentRating) {
    facts.push(item.contentRating);
  }
  if (item.genres) {
    facts.push(item.genres);
  }
  if (item.runtimeMinutes) {
    facts.push(formatRuntime(item.runtimeMinutes));
  }
  facts.push(item.extension);
  facts.push(formatBytes(item.bytes));

  if (probe && probe.ok) {
    if (probe.type && probe.type !== "unknown") {
      facts.push(probe.type);
    }
    if (probe.range && probe.range !== "none") {
      facts.push(`Ranges ${probe.range}`);
    }
  }

  return uniqueBits(facts);
}

function itemSummary(item) {
  if (item.overview) {
    return truncateText(item.overview, 260);
  }
  if (item.tagline) {
    return truncateText(item.tagline, 260);
  }
  if (item.section === "tv") {
    return joinBits([item.showTitle, item.seasonLabel, item.episodeNumber ? `Episode ${item.episodeNumber}` : ""]);
  }
  if (item.section === "music") {
    return joinBits([item.artist, item.album, item.year]) || "Queue this track directly from library storage.";
  }
  if (item.section === "audiobooks") {
    return (
      joinBits([
        item.artist,
        audiobookSeriesLabel(item) || audiobookCollectionName(item),
        audiobookNarratorLabel(item),
        item.publisher,
        item.year,
      ]) || "Play this audiobook directly from library storage."
    );
  }
  if (item.section === "documents") {
    if (item.type === "image") {
      return "Open this image or map directly from the portable file library.";
    }
    if (isPdfDocument(item)) {
      return "Open this PDF in a new browser tab for easier scrolling, paging, and zooming on mobile.";
    }
    return "Open this document directly from library storage.";
  }
  return "Stream this title directly from library storage.";
}

function documentExtension(item) {
  return String(item && item.extension ? item.extension : fileNameFromPath(item && item.path))
    .toLowerCase()
    .replace(/^\./, "")
    .trim();
}

function isPdfDocument(item) {
  return Boolean(item && item.type === "document" && documentExtension(item) === "pdf");
}

function openItemInNewTab(item) {
  if (!item || !item.streamUrl) {
    return;
  }

  const link = document.createElement("a");
  link.href = item.streamUrl;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

function renderPlayerDetails(item, probe) {
  els.playerFacts.innerHTML = "";
  els.playerActions.innerHTML = "";

  if (!item) {
    els.playerTitle.textContent = "Nothing selected yet";
    els.playerSummary.textContent =
      "Metadata, artwork, and file details will show up here when you open something from the library.";
    els.playerActions.hidden = true;
    return;
  }

  els.playerTitle.textContent = item.title;
  els.playerSummary.textContent = itemSummary(item);
  els.playerActions.hidden = !item.streamUrl;
  if (item.streamUrl) {
    if (isPdfDocument(item)) {
      els.playerActions.appendChild(createButton("Open PDF", "primary-button", () => openItemInNewTab(item)));
    }
    els.playerActions.appendChild(createButton("Download", "ghost-button", () => downloadMediaItem(item)));
  }

  for (const fact of itemDetailFacts(item, probe)) {
    els.playerFacts.appendChild(createFactPill(fact));
  }
}

function resetPlayers() {
  els.video.pause();
  els.audio.pause();
  els.video.style.display = "none";
  els.audio.style.display = "none";
  els.document.style.display = "none";
  els.image.style.display = "none";
  els.empty.style.display = "grid";
  els.video.removeAttribute("src");
  els.video.removeAttribute("poster");
  els.audio.removeAttribute("src");
  els.document.removeAttribute("src");
  els.document.src = "about:blank";
  els.image.removeAttribute("src");
  els.empty.textContent = "Pick something from the library to start opening files from the server.";
  els.playerCard.style.background = "";
  els.playerCard.classList.remove("player-card--artwork");
  els.video.load();
  els.audio.load();
}

function applyPlayerArtwork(item) {
  const artUrl =
    item && item.type !== "image" ? item.backdropUrl || item.posterUrl || "" : "";

  if (!artUrl) {
    els.playerCard.style.background = "";
    els.playerCard.classList.remove("player-card--artwork");
  } else {
    els.playerCard.style.background = createArtBackground(
      artUrl,
      item.path || item.title || "player",
      "landscape",
    );
    els.playerCard.classList.add("player-card--artwork");
  }

  if (item && item.type === "video") {
    const posterUrl = item.posterUrl || item.backdropUrl || "";
    if (posterUrl) {
      els.video.poster = posterUrl;
    } else {
      els.video.removeAttribute("poster");
    }
  }
}

async function probeStream(item) {
  try {
    const response = await fetch(item.streamUrl, {
      method: "HEAD",
      cache: "no-store",
    });
    return {
      ok: response.ok,
      status: response.status,
      type: response.headers.get("content-type") || "unknown",
      length: response.headers.get("content-length") || "unknown",
      range: response.headers.get("accept-ranges") || "none",
    };
  } catch (error) {
    return {
      ok: false,
      status: "network",
      type: "unknown",
      length: "unknown",
      range: "unknown",
      error,
    };
  }
}

async function playItem(item, options = {}) {
  persistActivePlaybackProgress(true, false, false);
  resetPlayers();
  state.playingItem = item;
  state.lastProgressSaveAt = 0;
  state.pendingResume = null;
  applyPlayerArtwork(item);
  renderPlayerDetails(item);

  if (!item.streamUrl) {
    els.empty.style.display = "grid";
    els.playerCaption.textContent = `${item.title} | no stream is available for this item`;
    return;
  }

  els.empty.style.display = "none";

  if (item.type === "video" || (item.type === "audio" && item.section === "audiobooks")) {
    const startAt =
      options.startAt != null
        ? Number(options.startAt)
        : options.resume === false
          ? 0
          : resumeTimeForItem(item);
    if (startAt > 0) {
      state.pendingResume = {
        path: item.path,
        time: startAt,
      };
    }
  }

  if (item.type === "video") {
    els.video.src = item.streamUrl;
    els.video.load();
    els.video.style.display = "block";
    els.video.play().catch(() => {});
  } else if (item.type === "audio") {
    els.audio.src = item.streamUrl;
    els.audio.load();
    els.audio.style.display = "block";
    els.audio.play().catch(() => {});
  } else if (item.type === "document") {
    if (isPdfDocument(item)) {
      openItemInNewTab(item);
      els.empty.style.display = "grid";
      els.empty.textContent = "This PDF opened in a new tab so your browser can handle scrolling, paging, and zooming.";
    } else {
      els.document.src = item.streamUrl;
      els.document.style.display = "block";
    }
  } else {
    els.image.src = item.streamUrl;
    els.image.style.display = "block";
  }

  els.playerCaption.textContent =
    joinBits([
      item.extension || "",
      item.bytes ? `${item.bytes} bytes` : "",
      isPdfDocument(item) ? "Opened in a new tab" : "",
      item.type === "video" || item.type === "audio" ? "Streaming now" : "",
    ]) || `${item.title} loaded`;

  renderPlayerDetails(item);
  document.querySelector(".player-section").scrollIntoView({ behavior: "smooth", block: "start" });
}

function attachPlayerDiagnostics() {
  const errorLabels = {
    1: "playback was aborted",
    2: "a network error interrupted the stream",
    3: "the browser could not decode the media",
    4: "the source was rejected",
  };

  function reportError(kind, element) {
    if (!state.playingItem) {
      return;
    }

    const code = element.error ? element.error.code : 0;
    const detail = errorLabels[code] || "the stream could not start";
    els.playerCaption.textContent = `${state.playingItem.title} | ${kind} error: ${detail}`;
  }

  els.video.addEventListener("error", () => reportError("Video", els.video));
  els.audio.addEventListener("error", () => reportError("Audio", els.audio));
  els.video.addEventListener("loadedmetadata", applyPendingPlaybackResume);
  els.video.addEventListener("canplay", applyPendingPlaybackResume);
  els.audio.addEventListener("loadedmetadata", applyPendingPlaybackResume);
  els.audio.addEventListener("canplay", applyPendingPlaybackResume);
  els.video.addEventListener("timeupdate", () => persistActivePlaybackProgress(false, false, false));
  els.video.addEventListener("pause", () => persistActivePlaybackProgress(true, false, false));
  els.video.addEventListener("ended", () => persistActivePlaybackProgress(true, true, true));
  els.audio.addEventListener("timeupdate", () => persistActivePlaybackProgress(false, false, false));
  els.audio.addEventListener("pause", () => persistActivePlaybackProgress(true, false, false));
  els.audio.addEventListener("ended", () => persistActivePlaybackProgress(true, true, true));
}

function clearSearch() {
  state.query = "";
  els.search.value = "";
}

function setGenreFilter(section, genre) {
  const safeSection = String(section || "").trim();
  if (!safeSection || !state.genreFilters || !Object.prototype.hasOwnProperty.call(state.genreFilters, safeSection)) {
    return;
  }

  const nextGenre = String(genre || "").trim();
  if (currentGenreFilter(safeSection) === nextGenre) {
    return;
  }

  state.genreFilters = {
    ...state.genreFilters,
    [safeSection]: nextGenre,
  };

  if (routeGenreSection() === safeSection && routeUsesServerSearch()) {
    prepareRouteDataForLoading();
    render();
    scheduleRouteQueryRefresh();
    return;
  }

  render();
}

function setSearchQuery(query) {
  state.query = String(query || "").trim();
  els.search.value = state.query;
  if (routeUsesServerSearch()) {
    prepareRouteDataForLoading();
    render();
    scheduleRouteQueryRefresh();
    return;
  }
  render();
}

function renderPageFilters() {
  if (!els.pageFilterSlot) {
    return;
  }

  const section = routeGenreSection();
  els.pageFilterSlot.innerHTML = "";
  els.pageFilterSlot.hidden = !section;
  els.pageFilterSlot.style.display = section ? "" : "none";
  if (!section) {
    return;
  }

  const field = document.createElement("div");
  field.className = "filter-field";

  const select = document.createElement("select");
  select.id = "genre-filter-input";
  select.name = "genre";
  select.setAttribute("aria-label", "Filter by genre");

  const allOption = document.createElement("option");
  allOption.value = "";
  allOption.textContent = "All genres";
  select.appendChild(allOption);

  const options = Array.isArray(state.catalogGenres[section]) ? state.catalogGenres[section] : [];
  const selectedGenre = currentGenreFilter(section);
  const values = selectedGenre && !options.some((genre) => genre.toLowerCase() === selectedGenre.toLowerCase())
    ? [selectedGenre, ...options]
    : options;

  for (const genre of values) {
    const option = document.createElement("option");
    option.value = genre;
    option.textContent = genre;
    select.appendChild(option);
  }

  select.value = selectedGenre;
  select.addEventListener("change", (event) => {
    setGenreFilter(section, event.target.value);
  });

  field.appendChild(select);
  els.pageFilterSlot.appendChild(field);
}

function openRoute(route, replace) {
  const path = typeof route === "string" ? route : buildRoutePath(route);
  const isNewPath = path !== window.location.pathname;
  if (routeQueryRefreshTimer) {
    window.clearTimeout(routeQueryRefreshTimer);
    routeQueryRefreshTimer = 0;
  }

  if (path !== window.location.pathname) {
    const method = replace ? "replaceState" : "pushState";
    window.history[method]({}, "", path);
  }

  if (isNewPath) {
    clearSearch();
  }

  state.route = parseRoute(window.location.pathname);
  prepareRouteDataForLoading();
  render();
  refreshAll().catch((error) => {
    els.pageSubtitle.textContent = `Unable to reach the server: ${error.message}`;
  });
}

function openMoviePage(item, options = {}) {
  const currentItem = findMediaItemByPath(item && item.path) || item;
  if (!currentItem || !currentItem.path) {
    return;
  }

  openRoute({ name: "movie", path: currentItem.path });

  if (!options.autoplay) {
    return;
  }

  const playbackItem = findMediaItemByPath(currentItem.path) || currentItem;
  const playOptions = {};
  if (options.resume === false) {
    playOptions.resume = false;
  }
  if (options.startAt != null) {
    playOptions.startAt = options.startAt;
  }
  playItem(playbackItem, playOptions);
}

function openAudiobookPage(item, options = {}) {
  const currentItem = findMediaItemByPath(item && item.path) || item;
  if (!currentItem || !currentItem.path) {
    return;
  }

  openRoute({ name: "audiobook", path: currentItem.path });

  if (!options.autoplay) {
    return;
  }

  const playbackItem = findMediaItemByPath(currentItem.path) || currentItem;
  const playOptions = {};
  if (options.resume === false) {
    playOptions.resume = false;
  }
  if (options.startAt != null) {
    playOptions.startAt = options.startAt;
  }
  playItem(playbackItem, playOptions);
}

function createButton(label, className, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}

function downloadMediaItem(item) {
  if (!item || !item.streamUrl) {
    return;
  }

  const link = document.createElement("a");
  link.href = item.streamUrl;
  link.download =
    fileNameFromPath(item.path) ||
    `${item.title || "download"}.${String(item.extension || "").toLowerCase()}`;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

function mediaCardMeta(item) {
  const bits = [];

  if (item.section === "tv" && item.showTitle) {
    bits.push(item.showTitle);
  }
  if (item.artist) {
    bits.push(item.artist);
  }
  if (item.album && item.section === "music") {
    bits.push(item.album);
  }
  if (item.section === "audiobooks") {
    const seriesLabel = audiobookSeriesLabel(item);
    if (seriesLabel) {
      bits.push(seriesLabel);
    } else if (item.album) {
      bits.push(item.album);
    }
    if (item.publisher) {
      bits.push(item.publisher);
    }
  }
  if (item.year) {
    bits.push(item.year);
  }
  if (item.contentRating) {
    bits.push(item.contentRating);
  }
  if (item.runtimeMinutes) {
    bits.push(formatRuntime(item.runtimeMinutes));
  }
  bits.push(item.extension);
  bits.push(formatBytes(item.bytes));

  return joinBits(bits);
}

function audiobookPathSegments(item) {
  const relative = relativeMediaPath(item && item.path);
  const segments = String(relative || "")
    .split("/")
    .map((part) => part.trim())
    .filter(Boolean);
  if (segments[0] && segments[0].toLowerCase() === "audiobooks") {
    segments.shift();
  }
  return segments;
}

function audiobookFolderSegments(item) {
  const segments = audiobookPathSegments(item);
  return segments.length > 1 ? segments.slice(0, -1) : [];
}

function audiobookAuthorName(item) {
  if (!item) {
    return "";
  }

  const artist = String(item.artist || "").trim();
  if (artist) {
    return artist;
  }

  const folders = audiobookFolderSegments(item);
  return folders.length ? titleFromPath(folders[0]) : "";
}

function audiobookCollectionName(item) {
  if (!item) {
    return "";
  }

  const seriesName = audiobookSeriesName(item);
  if (seriesName) {
    return seriesName;
  }

  const album = normalizeAudiobookCollectionLabel(item.album);
  if (album) {
    return album;
  }

  const folders = audiobookFolderSegments(item);
  if (folders.length >= 2) {
    return normalizeAudiobookCollectionLabel(titleFromPath(folders[1]));
  }

  return "";
}

function compareAudiobookSeriesOrder(left, right) {
  const leftValue = audiobookSeriesIndexValue(left);
  const rightValue = audiobookSeriesIndexValue(right);
  const leftIndex = Number.parseFloat(leftValue);
  const rightIndex = Number.parseFloat(rightValue);
  const leftHasIndex = leftValue !== "" && Number.isFinite(leftIndex);
  const rightHasIndex = rightValue !== "" && Number.isFinite(rightIndex);

  if (leftHasIndex && rightHasIndex && leftIndex !== rightIndex) {
    return leftIndex - rightIndex;
  }
  if (leftHasIndex !== rightHasIndex) {
    return leftHasIndex ? -1 : 1;
  }

  const titleOrder = compareText(left && (left.sortTitle || left.title), right && (right.sortTitle || right.title));
  if (titleOrder !== 0) {
    return titleOrder;
  }
  return compareText(left && left.path, right && right.path);
}

function firstAudiobookInCollection(items) {
  return [...(items || [])].sort(compareAudiobookSeriesOrder)[0] || null;
}

function buildAudiobookBrowseGroups(items, kind) {
  const groups = new Map();

  for (const item of items) {
    const label =
      kind === "author"
        ? audiobookAuthorName(item)
        : audiobookCollectionName(item);
    if (!label) {
      continue;
    }

    const author = audiobookAuthorName(item);
    const key = slugifyText(label);

    let group = groups.get(key);
    if (!group) {
      group = {
        key,
        kind,
        label,
        items: [],
        previewItem: item,
        authors: new Set(),
        collections: new Set(),
      };
      groups.set(key, group);
    }

    group.items.push(item);
    if (author) {
      group.authors.add(author);
    }
    const collectionName = audiobookCollectionName(item);
    if (collectionName) {
      group.collections.add(collectionName);
    }
    if (
      group.kind !== "collection" &&
      (
        !group.previewItem ||
        (!group.previewItem.posterUrl && item.posterUrl) ||
        (!group.previewItem.backdropUrl && item.backdropUrl)
      )
    ) {
      group.previewItem = item;
    }
  }

  return Array.from(groups.values())
    .map((group) => {
      const authors = Array.from(group.authors);
      const collections = Array.from(group.collections);
      const primaryAuthor = authors[0] || "";
      const previewItem = group.kind === "collection" ? firstAudiobookInCollection(group.items) : group.previewItem;
      const searchText = [
        group.label,
        ...authors,
        ...collections,
        ...group.items.map((item) => [item.title, item.artist, item.album, item.seriesName].filter(Boolean).join(" ")),
      ]
        .filter(Boolean)
        .join(" ");

      return {
        key: group.key,
        kind: group.kind,
        label: group.label,
        items: group.items,
        previewItem,
        authors,
        collections,
        primaryAuthor,
        count: group.items.length,
        searchText,
        filterQuery: group.label,
      };
    })
    .filter((group) => {
      if (group.kind !== "collection") {
        return true;
      }
      const previewTitle = String((group.previewItem && group.previewItem.title) || "").trim();
      return group.count > 1 || slugifyText(group.label) !== slugifyText(previewTitle);
    })
    .filter((group) => matchesQuery(group.searchText))
    .sort((left, right) => compareText(left.label, right.label));
}

function createAudiobookGroupCard(group) {
  const previewItem = group.previewItem || (group.items.length ? group.items[0] : null);
  const targetRoute =
    group.kind === "collection"
      ? { name: "audiobookCollection", label: group.label }
      : { name: "audiobookAuthor", label: group.label };
  const subtitle =
    group.kind === "collection"
      ? joinBits([
          group.primaryAuthor,
          countLabel(group.count, "audiobook"),
        ]) || "Browse this audiobook collection."
      : joinBits([
          countLabel(group.count, "audiobook"),
          group.collections.length ? countLabel(group.collections.length, "collection") : "",
        ]) || "Browse audiobooks from this author.";

  return createCard("media", {
    badge: group.kind === "collection" ? "Collection" : "Author",
    meta: group.kind === "collection"
      ? countLabel(group.count, "audiobook")
      : group.collections.length
        ? countLabel(group.collections.length, "collection")
        : countLabel(group.count, "audiobook"),
    title: group.label,
    subtitle,
    actionLabel: group.kind === "collection" ? "Open Collection" : "Open Author",
    gradientKey: `audiobook-group-${group.kind}-${group.key}`,
    imageUrl: previewItem ? previewItem.posterUrl || previewItem.backdropUrl : "",
    cardClassName: "movie-page-card audiobook-group-card",
    onPrimary: () => openRoute(targetRoute),
  });
}

function createAudiobookShelfCard(item) {
  return createMediaCard(item, {
    compact: true,
    includeYearInTitle: true,
    cardClassName: "movie-page-card",
  });
}

function relativeMediaPath(path) {
  return String(path || "").replace(/^\/media\//, "");
}

function normalizeDocumentFolder(folder) {
  return String(folder || "")
    .replace(/\\/g, "/")
    .split("/")
    .map((part) => part.trim())
    .filter(Boolean)
    .join("/");
}

function documentFolderSegments(folder) {
  return normalizeDocumentFolder(folder)
    .split("/")
    .filter(Boolean);
}

function documentRelativePath(path) {
  return String(path || "").replace(/^\/media\/(?:documents|photos)\//i, "");
}

function documentDirectoryPath(path) {
  const relativePath = normalizeDocumentFolder(documentRelativePath(path));
  if (!relativePath) {
    return "";
  }

  const segments = relativePath.split("/");
  segments.pop();
  return normalizeDocumentFolder(segments.join("/"));
}

function documentParentFolder(folder) {
  const segments = documentFolderSegments(folder);
  segments.pop();
  return normalizeDocumentFolder(segments.join("/"));
}

function documentFolderTitle(folder) {
  const segments = documentFolderSegments(folder);
  return segments.length ? segments[segments.length - 1] : "Field files";
}

function documentRoute(folder) {
  return {
    name: "documents",
    folder: normalizeDocumentFolder(folder),
  };
}

function buildDocumentBrowserState(folder) {
  const currentFolder = normalizeDocumentFolder(folder);
  const currentPrefix = currentFolder ? `${currentFolder}/` : "";
  const folderMap = new Map();
  const directFiles = [];
  let exists = currentFolder === "";

  for (const item of librarySections().documents) {
    const directory = documentDirectoryPath(item.path);
    const relativePath = normalizeDocumentFolder(documentRelativePath(item.path));

    if (!relativePath) {
      continue;
    }

    if (directory === currentFolder) {
      exists = true;
      directFiles.push(item);
      continue;
    }

    if (currentFolder) {
      if (!directory.startsWith(currentPrefix)) {
        continue;
      }

      exists = true;
      const remainder = directory.slice(currentPrefix.length);
      if (!remainder) {
        continue;
      }

      const childName = remainder.split("/")[0];
      const childPath = normalizeDocumentFolder(`${currentFolder}/${childName}`);
      let folderEntry = folderMap.get(childPath);
      if (!folderEntry) {
        folderEntry = {
          name: childName,
          path: childPath,
          itemCount: 0,
          directFileCount: 0,
          previewItem: item,
          searchText: "",
        };
        folderMap.set(childPath, folderEntry);
      }

      folderEntry.itemCount += 1;
      folderEntry.searchText = `${folderEntry.searchText} ${relativePath} ${item.title || ""} ${item.extension || ""}`.trim();
      if (directory === childPath) {
        folderEntry.directFileCount += 1;
      }
      continue;
    }

    if (!directory) {
      exists = true;
      directFiles.push(item);
      continue;
    }

    exists = true;
    const childName = directory.split("/")[0];
    const childPath = normalizeDocumentFolder(childName);
    let folderEntry = folderMap.get(childPath);
    if (!folderEntry) {
      folderEntry = {
        name: childName,
        path: childPath,
        itemCount: 0,
        directFileCount: 0,
        previewItem: item,
        searchText: "",
      };
      folderMap.set(childPath, folderEntry);
    }

    folderEntry.itemCount += 1;
    folderEntry.searchText = `${folderEntry.searchText} ${relativePath} ${item.title || ""} ${item.extension || ""}`.trim();
    if (directory === childPath) {
      folderEntry.directFileCount += 1;
    }
  }

  directFiles.sort((left, right) => compareText(left.sortTitle || left.title, right.sortTitle || right.title));
  const allFolders = Array.from(folderMap.values()).sort((left, right) => compareText(left.name, right.name));
  const folders = allFolders.filter((entry) =>
    matchesQuery([entry.name, entry.path, entry.searchText].filter(Boolean).join(" ")),
  );
  const files = filterMediaItems(directFiles);

  return {
    exists,
    currentFolder,
    parentFolder: documentParentFolder(currentFolder),
    title: documentFolderTitle(currentFolder),
    folders,
    files,
    totalFolders: allFolders.length,
    totalFiles: directFiles.length,
    previewItem: directFiles[0] || (allFolders[0] ? allFolders[0].previewItem : null) || null,
  };
}

function showCardMeta(show) {
  return joinBits([
    show.year,
    show.contentRating,
    `${show.seasonCount} season${show.seasonCount === 1 ? "" : "s"}`,
    `${show.episodeCount} episode${show.episodeCount === 1 ? "" : "s"}`,
  ]);
}

function showCardSubtitle(show) {
  if (show.overview) {
    return truncateText(show.overview, 180);
  }

  const first = firstEpisode(show);
  if (first) {
    return `Start with ${first.title} or open the show page to browse every season.`;
  }

  return "Open the show page to browse this library.";
}

function createCard(kind, config) {
  const node = els.cardTemplate.content.firstElementChild.cloneNode(true);
  const surface = node.querySelector(".card-surface");
  const art = node.querySelector(".card-art");
  const artImage = node.querySelector(".card-art-image");
  const copy = node.querySelector(".card-copy");
  const topLine = node.querySelector(".card-topline");
  const badge = node.querySelector(".badge");
  const meta = node.querySelector(".meta");
  const title = node.querySelector(".card-title");
  const subtitle = node.querySelector(".card-subtitle");
  const action = node.querySelector(".card-button");

  const isShow = kind === "show";
  const variant = isShow ? "landscape" : "portrait";
  node.classList.add(isShow ? "show-card" : "media-card--poster");
  if (config.cardClassName) {
    node.classList.add(
      ...String(config.cardClassName)
        .split(/\s+/)
        .filter(Boolean),
    );
  }
  if (config.compact) {
    node.classList.add("library-card--compact");
  }
  const showTopLine = !config.compact || Boolean(config.showTopLineWhenCompact);
  const showSubtitle = !config.compact || Boolean(config.showSubtitleWhenCompact);
  art.classList.add(isShow ? "card-art--show" : "card-art--poster");
  art.style.background = coverGradient(config.gradientKey, variant);
  art.classList.remove("card-art--loaded");
  artImage.loading = "lazy";
  artImage.decoding = "async";
  const markArtLoaded = () => {
    if (artImage.currentSrc || artImage.getAttribute("src")) {
      art.classList.add("card-art--loaded");
    }
  };
  artImage.onload = () => {
    markArtLoaded();
  };
  artImage.onerror = () => {
    art.classList.remove("card-art--loaded");
    artImage.removeAttribute("src");
  };
  if (config.imageUrl) {
    artImage.src = config.imageUrl;
    artImage.alt = `${config.title} artwork`;
    if (artImage.complete && artImage.naturalWidth > 0) {
      markArtLoaded();
    }
  } else {
    artImage.removeAttribute("src");
    artImage.alt = "";
  }

  if (config.artIcon) {
    art.appendChild(createCardArtIcon(config.artIcon));
  }

  const overlayChip =
    config.overlayChip && config.overlayChip.value
      ? createInfoChip(config.overlayChip.label || "", config.overlayChip.value, {
          className: ["rating-chip--overlay", config.overlayChip.className].filter(Boolean).join(" "),
          ariaLabel: config.overlayChip.ariaLabel,
        })
      : config.rating
        ? createRatingChip(config.rating, { className: "rating-chip--overlay" })
        : null;
  if (overlayChip) {
    art.appendChild(overlayChip);
  }

  badge.textContent = config.badge;
  meta.textContent = config.meta;
  meta.hidden = !config.meta;
  topLine.hidden = !showTopLine;
  topLine.classList.toggle("card-topline--badge-only", !config.meta);
  title.textContent = config.title;
  subtitle.textContent = config.subtitle;
  subtitle.hidden = !config.subtitle || !showSubtitle;

  if (config.watchState && typeof config.watchState.onToggle === "function") {
    const watchButton = document.createElement("button");
    watchButton.type = "button";
    watchButton.className = "card-watch-toggle";
    if (config.watchState.watched) {
      watchButton.classList.add("is-watched");
    }
    watchButton.textContent = "\u2713";
    watchButton.setAttribute("aria-label", config.watchState.label || "Toggle watched state");
    watchButton.setAttribute("title", config.watchState.label || "Toggle watched state");
    watchButton.setAttribute("aria-pressed", config.watchState.watched ? "true" : "false");
    watchButton.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      config.watchState.onToggle(event);
    });
    node.appendChild(watchButton);
  }

  if (config.progress) {
    const progressWrap = document.createElement("div");
    progressWrap.className = "card-progress";

    if (config.progress.label) {
      const progressLabelNode = document.createElement("p");
      progressLabelNode.className = "card-progress-label";
      progressLabelNode.textContent = config.progress.label;
      progressWrap.appendChild(progressLabelNode);
    }

    const progressTrack = document.createElement("div");
    progressTrack.className = "card-progress-track";
    const progressFill = document.createElement("span");
    progressFill.className = "card-progress-fill";
    progressFill.style.width = `${Math.max(0, Math.min(100, Number(config.progress.percent || 0)))}%`;
    progressTrack.appendChild(progressFill);
    progressWrap.appendChild(progressTrack);
    copy.appendChild(progressWrap);
  }

  if (config.onPrimary) {
    surface.addEventListener("click", config.onPrimary);
  }
  action.textContent = config.actionLabel;
  if (config.onAction || config.onPrimary) {
    action.addEventListener("click", (event) => {
      event.stopPropagation();
      (config.onAction || config.onPrimary)(event);
    });
  }
  return node;
}

function createMediaCard(item, options = {}) {
  if (item.section === "documents") {
    return createDocumentCard(item, options);
  }

  const badgeMap = {
    movies: "Movie",
    music: "Track",
    audiobooks: "Audiobook",
    tv: "Episode",
  };
  const compact = Boolean(options.compact) || item.section === "movies";
  const includeYearInTitle = Boolean(options.includeYearInTitle);
  const title = includeYearInTitle ? titleWithYear(item.title, item.year) : item.title;
  const opensMovieDetail = item.section === "movies";
  const opensAudiobookDetail = item.section === "audiobooks";
  const resumeProgress =
    item.section === "movies" || item.section === "audiobooks" ? resumeProgressForItem(item) : null;
  const compactMeta = options.compactMeta != null ? String(options.compactMeta || "").trim() : "";
  const audiobookBookNumber = item.section === "audiobooks" ? audiobookSeriesIndexValue(item) : "";
  const cardClassName = [
    item.section === "audiobooks" ? "audiobook-cover-card" : "",
    options.cardClassName || "",
  ]
    .filter(Boolean)
    .join(" ");

  return createCard("media", {
    badge: badgeMap[item.section] || item.type,
    meta: compact ? compactMeta : mediaCardMeta(item),
    title,
    subtitle: compact ? "" : itemSummary(item),
    compact,
    showTopLineWhenCompact: Boolean(options.showTopLineWhenCompact) || Boolean(compactMeta),
    actionLabel: primaryActionLabelForItem(item),
    gradientKey: `${item.section}-${item.title}-${item.path}`,
    imageUrl: item.posterUrl || item.backdropUrl,
    cardClassName,
    overlayChip:
      audiobookBookNumber
        ? {
            label: "Book",
            value: audiobookBookNumber,
            ariaLabel: `Book ${audiobookBookNumber}`,
          }
        : null,
    rating: item.section === "movies" ? item.tmdbRating : 0,
    progress: resumeProgress
      ? {
          percent: resumeProgress.percent,
          label: compact ? "" : resumeProgress.label,
        }
      : null,
    watchState: movieOrEpisodeWatchState(item),
    onPrimary:
      opensMovieDetail
        ? () => openMoviePage(item)
        : opensAudiobookDetail
          ? () => openAudiobookPage(item)
          : () => playItem(item),
    onAction:
      opensMovieDetail
        ? () => openMoviePage(item, { autoplay: true })
        : opensAudiobookDetail
          ? () => openAudiobookPage(item, { autoplay: true })
          : () => playItem(item),
  });
}

function createDocumentCard(item, options = {}) {
  const cardClassName = ["movie-page-card", "file-card", options.cardClassName].filter(Boolean).join(" ");
  const pdfItem = isPdfDocument(item);
  return createCard("media", {
    badge: item.type === "image" ? "Image" : item.extension || "File",
    meta: joinBits([item.type === "image" ? "Image" : "Document", item.extension, formatBytes(item.bytes)]),
    title: item.title,
    subtitle: truncateText(documentRelativePath(item.path), 90) || itemSummary(item),
    compact: Boolean(options.compact),
    actionLabel: item.type === "image" ? "Open Preview" : pdfItem ? "Open PDF" : "Open File",
    gradientKey: `document-${item.path}`,
    imageUrl: item.type === "image" ? buildAssetUrl(item.path) : pdfItem ? "" : item.posterUrl || item.backdropUrl,
    artIcon: pdfItem ? "pdf" : "",
    cardClassName,
    onPrimary: () => playItem(item),
  });
}

function createFolderCard(folder) {
  const summary =
    folder.directFileCount > 0 && folder.directFileCount !== folder.itemCount
      ? `${countLabel(folder.directFileCount, "file")} here, ${countLabel(folder.itemCount, "file")} total`
      : `${countLabel(folder.itemCount, "file")} ready here`;

  return createCard("media", {
    badge: "Folder",
    meta: summary,
    title: folder.name,
    subtitle: truncateText(folder.path, 90) || "Open this folder to browse the files inside it.",
    actionLabel: "Open Folder",
    gradientKey: `folder-${folder.path}`,
    imageUrl: "",
    artIcon: "folder",
    cardClassName: "movie-page-card folder-card",
    onPrimary: () => openRoute(documentRoute(folder.path)),
  });
}

function createContinueWatchingCard(entry) {
  const { item } = entry;
  const opensMovieDetail = item.section === "movies";

  if (entry.kind === "next") {
    return createCard("media", {
      badge: "",
      meta: "",
      title: joinBits([item.showTitle, episodeCardTitle(item)]) || episodeCardTitle(item),
      subtitle: "",
      compact: true,
      actionLabel: "Play Next",
      gradientKey: `next-${item.path}`,
      imageUrl: item.posterUrl || item.backdropUrl,
      cardClassName: "continue-card",
      watchState: movieOrEpisodeWatchState(item),
      onPrimary: () => playItem(item, { resume: false, startAt: 0 }),
    });
  }

  const entryProgress = entry.entry;
  return createCard("media", {
    badge: "Resume",
    meta: "",
    title: item.title,
    subtitle: "",
    actionLabel: "Resume",
    gradientKey: `resume-${item.path}`,
    imageUrl: item.posterUrl || item.backdropUrl,
    cardClassName: "continue-card",
    progress: {
      percent: progressPercent(entryProgress),
      label: progressLabel(entryProgress),
    },
    watchState: movieOrEpisodeWatchState(item),
    onPrimary: opensMovieDetail ? () => openMoviePage(item) : () => playItem(item),
    onAction: opensMovieDetail ? () => openMoviePage(item, { autoplay: true }) : () => playItem(item),
  });
}

function createShowCard(show, options = {}) {
  const compact = Boolean(options.compact);
  const posterLayout = Boolean(options.posterLayout);
  const includeYearInTitle = Boolean(options.includeYearInTitle);
  const title = includeYearInTitle ? titleWithYear(show.title, show.year) : show.title;
  const resumeEntry = resumeEntryForShow(show);
  const detailRoute = show.detailUrl || buildRoutePath({ name: "show", slug: show.slug });

  return createCard(posterLayout ? "media" : "show", {
    badge: "Series",
    meta: compact ? "" : showCardMeta(show),
    title,
    subtitle: compact ? "" : showCardSubtitle(show),
    compact,
    actionLabel: resumeEntry ? "Resume" : "Open Show",
    gradientKey: `show-${show.slug}`,
    imageUrl: posterLayout ? show.posterUrl || show.backdropUrl : show.backdropUrl || show.posterUrl,
    cardClassName: options.cardClassName || "",
    rating: show.tmdbRating,
    watchState: showWatchState(show),
    onPrimary: () => openRoute(detailRoute),
    onAction: resumeEntry ? () => playItem(resumeEntry.item) : () => openRoute(detailRoute),
  });
}

function createSeasonCard(show, season) {
  return createCard("media", {
    badge: "Season",
    meta: "",
    title: season.label || "Season",
    subtitle: "",
    compact: true,
    actionLabel: "Open Season",
    gradientKey: `season-${show.slug}-${seasonKeyForSeason(season)}`,
    imageUrl: show.posterUrl || show.backdropUrl,
    cardClassName: "movie-page-card",
    watchState: seasonWatchState(season),
    onPrimary: () => openRoute(seasonRoute(show, season)),
  });
}

function createEpisodeCard(item) {
  return createCard("media", {
    badge: "Episode",
    meta: "",
    title: episodeCardTitle(item),
    subtitle: "",
    compact: true,
    actionLabel: primaryActionLabelForItem(item),
    gradientKey: `episode-${item.path}`,
    imageUrl: item.posterUrl || item.backdropUrl,
    cardClassName: "movie-page-card",
    watchState: movieOrEpisodeWatchState(item),
    onPrimary: () => playItem(item),
  });
}

function createEpisodeRow(item) {
  const node = els.episodeTemplate.content.firstElementChild.cloneNode(true);
  const kicker = node.querySelector(".episode-kicker");
  const title = node.querySelector(".episode-title");
  const meta = node.querySelector(".episode-meta");
  const summary = node.querySelector(".episode-summary");
  const button = node.querySelector(".episode-button");

  kicker.textContent = joinBits([
    item.seasonLabel || "Season",
    item.episodeNumber ? `Episode ${item.episodeNumber}` : "Episode",
  ]);
  title.textContent = item.title;
  meta.textContent = joinBits([
    item.year,
    item.contentRating,
    formatRuntime(item.runtimeMinutes),
    item.extension,
    formatBytes(item.bytes),
  ]);
  summary.textContent = truncateText(itemSummary(item), 180);
  button.addEventListener("click", () => playItem(item));
  return node;
}

function createSectionHeading(eyebrow, title, subtitle) {
  const wrap = document.createElement("div");
  wrap.className = "section-heading";

  const left = document.createElement("div");
  const heading = document.createElement("h3");
  heading.textContent = title;
  if (eyebrow) {
    const eye = document.createElement("p");
    eye.className = "eyebrow";
    eye.textContent = eyebrow;
    left.appendChild(eye);
  }
  left.appendChild(heading);

  wrap.appendChild(left);
  if (subtitle) {
    const copy = document.createElement("p");
    copy.textContent = subtitle;
    wrap.appendChild(copy);
  }
  return wrap;
}

function appendGridSection(container, config) {
  const section = document.createElement("section");
  section.className = "content-section";
  section.appendChild(createSectionHeading(config.eyebrow, config.title, config.subtitle));

  const grid = document.createElement("div");
  grid.className = config.gridClass || "poster-grid";

  if (!config.items.length) {
    grid.appendChild(createEmptyState(config.emptyMessage || "Nothing matches this view yet."));
  } else {
    for (const item of config.items) {
      grid.appendChild(config.renderItem(item));
    }
  }

  section.appendChild(grid);
  container.appendChild(section);
}

function appendCarouselSection(container, config) {
  const section = document.createElement("section");
  section.className = "content-section";
  section.appendChild(createSectionHeading(config.eyebrow, config.title, config.subtitle));

  const scroller = document.createElement("div");
  scroller.className = config.scrollerClass || "episode-carousel";

  if (!config.items.length) {
    scroller.appendChild(createEmptyState(config.emptyMessage || "Nothing matches this view yet."));
  } else {
    for (const item of config.items) {
      const card = config.renderItem(item);
      if (config.itemClassName) {
        card.classList.add(
          ...String(config.itemClassName)
            .split(/\s+/)
            .filter(Boolean),
        );
      }
      scroller.appendChild(card);
    }
  }

  section.appendChild(scroller);
  container.appendChild(section);
}

function createEmptyState(message) {
  const empty = document.createElement("div");
  empty.className = "empty-state";
  empty.textContent = message;
  return empty;
}

function delay(ms) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

async function rescanLibrary() {
  els.pageSubtitle.textContent = "Rescanning the media library and refreshing metadata...";
  state.preferServerLibrary = true;
  scheduleDeviceStatusPolling(250);
  const response = await fetch("/api/rescan", { method: "POST", cache: "no-store" });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Rescan returned HTTP ${response.status}`);
  }
  await refreshAll();
  if (payload.message) {
    els.pageSubtitle.textContent = payload.message;
  }
  return payload;
}

async function refreshDeviceData() {
  els.pageSubtitle.textContent = "Refreshing device status and library information...";
  await refreshAll();
}

async function saveDeviceConfig(values) {
  const response = await fetch("/api/device-config", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    cache: "no-store",
    body: JSON.stringify(values),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Device config returned HTTP ${response.status}`);
  }
  return payload;
}

async function loadDeviceConfig() {
  const response = await fetch("/api/device-config", { cache: "no-store" });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Device config returned HTTP ${response.status}`);
  }
  return payload.config || {};
}

async function loadCatalogGenres() {
  const payload = await fetchCatalogJson("/api/catalog/genres", "Catalog genres");
  state.catalogGenres = {
    movies: Array.isArray(payload.movies) ? payload.movies : [],
    tv: Array.isArray(payload.tv) ? payload.tv : [],
    audiobooks: Array.isArray(payload.audiobooks) ? payload.audiobooks : [],
  };
}

function createEmptyCatalogPageState(limit = CATALOG_PAGE_SIZE) {
  return {
    query: "",
    genre: "",
    total: 0,
    offset: 0,
    limit,
    count: 0,
    hasMore: false,
    items: [],
    loaded: false,
    loadingMore: false,
  };
}

function createEmptyCatalogProgressState() {
  return {
    count: 0,
    showCount: 0,
    items: [],
    shows: [],
    loaded: false,
  };
}

function prepareRouteDataForLoading() {
  if (routeUsesCatalogSummary()) {
    state.catalogSummary = null;
  }
  if (state.route.name === "home") {
    state.catalogHome = null;
    state.catalogProgress = state.query
      ? {
          ...createEmptyCatalogProgressState(),
          loaded: true,
        }
      : createEmptyCatalogProgressState();
    state.catalogSearch = {
      query: state.query,
      count: 0,
      items: [],
      loaded: false,
    };
    state.library = null;
    return;
  }
  if (state.route.name === "movies") {
    state.catalogMovies = createEmptyCatalogPageState();
    state.catalogMovies.query = state.query;
    state.catalogMovies.genre = currentGenreFilter("movies");
    state.library = null;
    return;
  }
  if (state.route.name === "movie") {
    state.catalogMovie = null;
    state.catalogMovieLoaded = false;
    state.library = null;
    return;
  }
  if (isAudiobookBrowseRoute()) {
    state.catalogAudiobooks = createEmptyCatalogPageState();
    state.catalogAudiobooks.query = state.query;
    state.catalogAudiobooks.genre = currentGenreFilter("audiobooks");
    state.library = null;
    return;
  }
  if (state.route.name === "audiobook") {
    state.catalogAudiobook = null;
    state.catalogAudiobookLoaded = false;
    state.library = null;
    return;
  }
  if (state.route.name === "tv") {
    state.catalogShows = createEmptyCatalogPageState();
    state.catalogShows.query = state.query;
    state.catalogShows.genre = currentGenreFilter("tv");
    state.library = null;
    return;
  }
  if (state.route.name === "show" || state.route.name === "season") {
    state.catalogShow = null;
    state.catalogShowLoaded = false;
    state.library = null;
  }
}

async function fetchCatalogJson(url, label) {
  const response = await fetch(url, { cache: "no-store" });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `${label} returned HTTP ${response.status}`);
  }
  return payload;
}

async function loadCatalogSummary() {
  const payload = await fetchCatalogJson("/api/catalog/summary", "Catalog summary");
  state.catalogSummary = {
    counts: payload.counts || null,
    metadata: payload.metadata || null,
  };
}

async function loadCatalogHome() {
  const payload = await fetchCatalogJson("/api/catalog/home", "Catalog home");
  state.catalogHome = {
    counts: payload.counts || null,
    metadata: payload.metadata || null,
    sections: payload.sections || {},
  };
}

async function loadCatalogProgressLookup() {
  const paths = playbackCatalogPaths();
  if (!paths.length) {
    state.catalogProgress = {
      ...createEmptyCatalogProgressState(),
      loaded: true,
    };
    return;
  }

  const response = await fetch("/api/catalog/lookup", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    cache: "no-store",
    body: JSON.stringify({ paths }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Catalog lookup returned HTTP ${response.status}`);
  }

  state.catalogProgress = {
    count: Number(payload.count || 0),
    showCount: Number(payload.showCount || 0),
    items: Array.isArray(payload.items) ? payload.items : [],
    shows: Array.isArray(payload.shows) ? payload.shows : [],
    loaded: true,
  };
}

async function loadCatalogSearch(query = state.query) {
  const safeQuery = String(query || "").trim();
  if (!safeQuery) {
    state.catalogSearch = {
      query: "",
      count: 0,
      items: [],
      loaded: true,
    };
    return;
  }
  const payload = await fetchCatalogJson(
    `/api/catalog/search?q=${encodeURIComponent(safeQuery)}&limit=${CATALOG_SEARCH_LIMIT}`,
    "Catalog search",
  );
  state.catalogSearch = {
    query: safeQuery,
    count: Number(payload.count || 0),
    items: Array.isArray(payload.items) ? payload.items : [],
    loaded: true,
  };
}

async function loadCatalogMovies(options = {}) {
  const append = Boolean(options.append);
  const safeQuery = String(options.query != null ? options.query : state.query).trim();
  const safeGenre = String(options.genre != null ? options.genre : currentGenreFilter("movies")).trim();
  const existing = state.catalogMovies || createEmptyCatalogPageState();
  const limit = Number(options.limit || existing.limit || CATALOG_PAGE_SIZE) || CATALOG_PAGE_SIZE;
  const offset = append ? Number(existing.offset || 0) : Number(options.offset || 0);
  if (!append) {
    state.catalogMovies = {
      ...createEmptyCatalogPageState(limit),
      query: safeQuery,
      genre: safeGenre,
    };
  } else {
    state.catalogMovies = {
      ...existing,
      loadingMore: true,
    };
  }
  const payload = await fetchCatalogJson(
    `/api/catalog/movies?offset=${offset}&limit=${limit}&q=${encodeURIComponent(safeQuery)}&genre=${encodeURIComponent(safeGenre)}`,
    "Movies",
  );
  state.catalogMovies = {
    query: safeQuery,
    genre: String(payload.genre != null ? payload.genre : safeGenre).trim(),
    total: Number(payload.total || 0),
    offset: Number(payload.offset || 0) + Number(payload.count || 0),
    limit: Number(payload.limit || limit),
    count: Number(payload.count || 0),
    hasMore: Boolean(payload.hasMore),
    items: append ? [...(existing.items || []), ...(payload.items || [])] : Array.isArray(payload.items) ? payload.items : [],
    loaded: true,
    loadingMore: false,
  };
}

async function loadCatalogShows(options = {}) {
  const append = Boolean(options.append);
  const safeQuery = String(options.query != null ? options.query : state.query).trim();
  const safeGenre = String(options.genre != null ? options.genre : currentGenreFilter("tv")).trim();
  const existing = state.catalogShows || createEmptyCatalogPageState();
  const limit = Number(options.limit || existing.limit || CATALOG_PAGE_SIZE) || CATALOG_PAGE_SIZE;
  const offset = append ? Number(existing.offset || 0) : Number(options.offset || 0);
  if (!append) {
    state.catalogShows = {
      ...createEmptyCatalogPageState(limit),
      query: safeQuery,
      genre: safeGenre,
    };
  } else {
    state.catalogShows = {
      ...existing,
      loadingMore: true,
    };
  }
  const payload = await fetchCatalogJson(
    `/api/catalog/shows?offset=${offset}&limit=${limit}&q=${encodeURIComponent(safeQuery)}&genre=${encodeURIComponent(safeGenre)}`,
    "Shows",
  );
  state.catalogShows = {
    query: safeQuery,
    genre: String(payload.genre != null ? payload.genre : safeGenre).trim(),
    total: Number(payload.total || 0),
    offset: Number(payload.offset || 0) + Number(payload.count || 0),
    limit: Number(payload.limit || limit),
    count: Number(payload.count || 0),
    hasMore: Boolean(payload.hasMore),
    items: append ? [...(existing.items || []), ...(payload.items || [])] : Array.isArray(payload.items) ? payload.items : [],
    loaded: true,
    loadingMore: false,
  };
}

async function loadCatalogAudiobooks(options = {}) {
  const append = Boolean(options.append);
  const safeQuery = String(options.query != null ? options.query : state.query).trim();
  const safeGenre = String(options.genre != null ? options.genre : currentGenreFilter("audiobooks")).trim();
  const browseTarget = currentAudiobookBrowseTarget();
  const safeCollection = String(options.collection != null ? options.collection : browseTarget.kind === "collection" ? browseTarget.label : "").trim();
  const safeAuthor = String(options.author != null ? options.author : browseTarget.kind === "author" ? browseTarget.label : "").trim();
  const existing = state.catalogAudiobooks || createEmptyCatalogPageState();
  const limit = Number(options.limit || existing.limit || CATALOG_PAGE_SIZE) || CATALOG_PAGE_SIZE;
  const offset = append ? Number(existing.offset || 0) : Number(options.offset || 0);
  if (!append) {
    state.catalogAudiobooks = {
      ...createEmptyCatalogPageState(limit),
      query: safeQuery,
      genre: safeGenre,
      collection: safeCollection,
      author: safeAuthor,
    };
  } else {
    state.catalogAudiobooks = {
      ...existing,
      loadingMore: true,
    };
  }
  const payload = await fetchCatalogJson(
    `/api/catalog/audiobooks?offset=${offset}&limit=${limit}&q=${encodeURIComponent(safeQuery)}&genre=${encodeURIComponent(safeGenre)}&collection=${encodeURIComponent(safeCollection)}&author=${encodeURIComponent(safeAuthor)}`,
    "Audiobooks",
  );
  state.catalogAudiobooks = {
    query: safeQuery,
    genre: String(payload.genre != null ? payload.genre : safeGenre).trim(),
    collection: String(payload.collection != null ? payload.collection : safeCollection).trim(),
    author: String(payload.author != null ? payload.author : safeAuthor).trim(),
    total: Number(payload.total || 0),
    offset: Number(payload.offset || 0) + Number(payload.count || 0),
    limit: Number(payload.limit || limit),
    count: Number(payload.count || 0),
    hasMore: Boolean(payload.hasMore),
    items: append ? [...(existing.items || []), ...(payload.items || [])] : Array.isArray(payload.items) ? payload.items : [],
    loaded: true,
    loadingMore: false,
  };
}

async function loadCatalogMovie(path) {
  const safePath = String(path || "").trim();
  state.catalogMovie = null;
  state.catalogMovieLoaded = false;
  if (!safePath) {
    state.catalogMovieLoaded = true;
    return;
  }
  const response = await fetch(`/api/catalog/movie?path=${encodeURIComponent(safePath)}`, { cache: "no-store" });
  const payload = await response.json().catch(() => ({}));
  if (response.status === 404) {
    state.catalogMovie = null;
    state.catalogMovieLoaded = true;
    return;
  }
  if (!response.ok) {
    throw new Error(payload.error || `Movie detail returned HTTP ${response.status}`);
  }
  state.catalogMovie = payload.item || null;
  state.catalogMovieLoaded = true;
}

async function loadCatalogAudiobook(path) {
  const safePath = String(path || "").trim();
  state.catalogAudiobook = null;
  state.catalogAudiobookLoaded = false;
  if (!safePath) {
    state.catalogAudiobookLoaded = true;
    return;
  }
  const response = await fetch(`/api/catalog/audiobook?path=${encodeURIComponent(safePath)}`, { cache: "no-store" });
  const payload = await response.json().catch(() => ({}));
  if (response.status === 404) {
    state.catalogAudiobook = null;
    state.catalogAudiobookLoaded = true;
    return;
  }
  if (!response.ok) {
    throw new Error(payload.error || `Audiobook detail returned HTTP ${response.status}`);
  }
  state.catalogAudiobook = payload.item || null;
  state.catalogAudiobookLoaded = true;
}

async function loadCatalogShow(slug) {
  const safeSlug = String(slug || "").trim();
  state.catalogShow = null;
  state.catalogShowLoaded = false;
  if (!safeSlug) {
    state.catalogShowLoaded = true;
    return;
  }
  const response = await fetch(`/api/catalog/show?slug=${encodeURIComponent(safeSlug)}`, { cache: "no-store" });
  const payload = await response.json().catch(() => ({}));
  if (response.status === 404) {
    state.catalogShow = null;
    state.catalogShowLoaded = true;
    return;
  }
  if (!response.ok) {
    throw new Error(payload.error || `Show detail returned HTTP ${response.status}`);
  }
  state.catalogShow = payload.show || null;
  state.catalogShowLoaded = true;
}

async function loadRandomCatalogItem(section = "") {
  const suffix = section ? `?section=${encodeURIComponent(section)}` : "";
  const payload = await fetchCatalogJson(`/api/catalog/random${suffix}`, "Random media");
  return payload.item || null;
}

async function loadRouteData() {
  if (state.route.name === "home") {
    if (state.query) {
      await Promise.all([loadCatalogSummary(), loadCatalogSearch(state.query)]);
      state.catalogHome = null;
      state.catalogProgress = {
        ...createEmptyCatalogProgressState(),
        loaded: true,
      };
    } else {
      await Promise.all([loadCatalogSummary(), loadCatalogHome(), loadCatalogProgressLookup()]);
      state.catalogSearch = {
        query: "",
        count: 0,
        items: [],
        loaded: true,
      };
    }
    return;
  }
  if (state.route.name === "movies") {
    await Promise.all([loadCatalogSummary(), loadCatalogMovies({ query: state.query, genre: currentGenreFilter("movies") })]);
    return;
  }
  if (state.route.name === "movie") {
    await Promise.all([loadCatalogSummary(), loadCatalogMovie(state.route.path)]);
    return;
  }
  if (isAudiobookBrowseRoute()) {
    await Promise.all([
      loadCatalogSummary(),
      loadCatalogAudiobooks({
        query: state.query,
        genre: currentGenreFilter("audiobooks"),
      }),
    ]);
    return;
  }
  if (state.route.name === "audiobook") {
    await Promise.all([loadCatalogSummary(), loadCatalogAudiobook(state.route.path)]);
    return;
  }
  if (state.route.name === "tv") {
    await Promise.all([loadCatalogSummary(), loadCatalogShows({ query: state.query, genre: currentGenreFilter("tv") })]);
    return;
  }
  if (state.route.name === "show" || state.route.name === "season") {
    await Promise.all([loadCatalogSummary(), loadCatalogShow(state.route.slug)]);
    return;
  }
  await loadLibrary();
}

function scheduleRouteQueryRefresh() {
  if (routeQueryRefreshTimer) {
    window.clearTimeout(routeQueryRefreshTimer);
    routeQueryRefreshTimer = 0;
  }
  routeQueryRefreshTimer = window.setTimeout(() => {
    routeQueryRefreshTimer = 0;
    prepareRouteDataForLoading();
    render();
    refreshAll().catch((error) => {
      els.pageSubtitle.textContent = `Refresh failed: ${error.message}`;
    });
  }, 220);
}

async function loadMoreCatalogMovies() {
  if (!state.catalogMovies.loaded || !state.catalogMovies.hasMore || state.catalogMovies.loadingMore) {
    return;
  }
  await loadCatalogMovies({
    append: true,
    query: state.catalogMovies.query || state.query,
    genre: state.catalogMovies.genre || currentGenreFilter("movies"),
  });
  render();
}

async function loadMoreCatalogShows() {
  if (!state.catalogShows.loaded || !state.catalogShows.hasMore || state.catalogShows.loadingMore) {
    return;
  }
  await loadCatalogShows({
    append: true,
    query: state.catalogShows.query || state.query,
    genre: state.catalogShows.genre || currentGenreFilter("tv"),
  });
  render();
}

async function loadMoreCatalogAudiobooks() {
  if (!state.catalogAudiobooks.loaded || !state.catalogAudiobooks.hasMore || state.catalogAudiobooks.loadingMore) {
    return;
  }
  const browseTarget = currentAudiobookBrowseTarget();
  await loadCatalogAudiobooks({
    append: true,
    query: state.catalogAudiobooks.query || state.query,
    genre: state.catalogAudiobooks.genre || currentGenreFilter("audiobooks"),
    collection:
      String(state.catalogAudiobooks.collection || "").trim() ||
      (browseTarget.kind === "collection" ? browseTarget.label : ""),
    author:
      String(state.catalogAudiobooks.author || "").trim() ||
      (browseTarget.kind === "author" ? browseTarget.label : ""),
  });
  render();
}

function disconnectCatalogAutoLoadObserver() {
  if (catalogAutoLoadObserver) {
    catalogAutoLoadObserver.disconnect();
    catalogAutoLoadObserver = null;
  }
}

function observeCatalogAutoLoad(target, loadMore) {
  disconnectCatalogAutoLoadObserver();
  if (!target || typeof loadMore !== "function" || typeof window.IntersectionObserver !== "function") {
    return;
  }

  catalogAutoLoadObserver = new window.IntersectionObserver(
    (entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) {
        return;
      }
      disconnectCatalogAutoLoadObserver();
      Promise.resolve(loadMore()).catch((error) => {
        if (els.pageSubtitle) {
          els.pageSubtitle.textContent = `Could not load more items: ${error.message}`;
        }
      });
    },
    {
      rootMargin: "240px 0px",
      threshold: 0.1,
    },
  );
  catalogAutoLoadObserver.observe(target);
}

function createUploadId() {
  return `upload-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function postSharedUploadProgress(payload) {
  return fetch("/api/upload-progress", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  }).catch(() => null);
}

async function uploadLibraryFiles(destination, entries, handlers = {}) {
  const uploadId = createUploadId();
  const totalBytes = entries.reduce((sum, entry) => sum + Math.max(Number((entry.file && entry.file.size) || 0) || 0, 0), 0);
  const formData = new FormData();
  formData.append("uploadId", uploadId);
  formData.append("destination", destination);
  for (const entry of entries) {
    formData.append("files", entry.file, entry.file.name);
    formData.append("relativePaths", entry.relativePath || entry.file.name);
  }
  let lastProgressSentAt = 0;

  const emitProgress = (update) => {
    if (typeof handlers.onProgress === "function") {
      handlers.onProgress(update);
    }
  };

  const progressUpdate = (update) => ({
    uploadId,
    destination,
    fileCount: entries.length,
    ...update,
  });

  const sendProgress = (update, force = false) => {
    const now = Date.now();
    if (!force && now - lastProgressSentAt < 400) {
      return;
    }
    lastProgressSentAt = now;
    void postSharedUploadProgress({
      uploadId,
      destination,
      fileCount: entries.length,
      bytesTotal: totalBytes,
      ...update,
    });
  };

  const initialMessage = `Starting upload of ${entries.length} file${entries.length === 1 ? "" : "s"}...`;
  emitProgress(progressUpdate({
    phase: "uploading",
    bytesSent: 0,
    bytesTotal: totalBytes,
    percent: 0,
    message: initialMessage,
  }));
  sendProgress(
    {
      phase: "uploading",
      bytesSent: 0,
      message: initialMessage,
    },
    true,
  );

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload");
    xhr.responseType = "json";

    const parsePayload = () => {
      if (xhr.response && typeof xhr.response === "object") {
        return xhr.response;
      }
      if (!xhr.responseText) {
        return {};
      }
      try {
        return JSON.parse(xhr.responseText);
      } catch (_error) {
        return {};
      }
    };

    xhr.upload.addEventListener("progress", (event) => {
      const bytesTotal = Math.max(event.lengthComputable ? event.total : 0, totalBytes);
      const bytesSent = Math.max(0, Math.min(event.loaded || 0, bytesTotal || event.loaded || 0));
      const percent = bytesTotal > 0 ? Math.round((bytesSent / bytesTotal) * 100) : 0;
      const message = percent
        ? `Uploading ${entries.length} file${entries.length === 1 ? "" : "s"}... ${percent}%`
        : `Uploading ${entries.length} file${entries.length === 1 ? "" : "s"}...`;
      const update = progressUpdate({
        phase: "uploading",
        bytesSent,
        bytesTotal,
        percent,
        message,
      });
      emitProgress(update);
      sendProgress(
        {
          phase: "uploading",
          bytesSent,
          bytesTotal,
          message,
        },
        percent >= 100,
      );
    });

    xhr.upload.addEventListener("load", () => {
      const message = "Transfer finished. The Pi is saving files and rescanning the library...";
      const update = progressUpdate({
        phase: "processing",
        bytesSent: totalBytes,
        bytesTotal: totalBytes,
        percent: 100,
        message,
      });
      emitProgress(update);
      sendProgress(
        {
          phase: "processing",
          bytesSent: totalBytes,
          bytesTotal: totalBytes,
          message,
        },
        true,
      );
    });

    xhr.addEventListener("load", () => {
      const payload = parsePayload();
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(payload);
        return;
      }

      const errorMessage = payload.error || `Upload failed with HTTP ${xhr.status}`;
      emitProgress(progressUpdate({
        phase: "error",
        bytesSent: totalBytes,
        bytesTotal: totalBytes,
        percent: 100,
        message: errorMessage,
        error: errorMessage,
      }));
      sendProgress(
        {
          phase: "error",
          bytesSent: totalBytes,
          bytesTotal: totalBytes,
          message: errorMessage,
          error: errorMessage,
        },
        true,
      );
      reject(new Error(errorMessage));
    });

    xhr.addEventListener("error", () => {
      const errorMessage = "Upload failed because the connection to the Pi was interrupted.";
      emitProgress(progressUpdate({
        phase: "error",
        bytesSent: 0,
        bytesTotal: totalBytes,
        percent: 0,
        message: errorMessage,
        error: errorMessage,
      }));
      sendProgress(
        {
          phase: "error",
          bytesSent: 0,
          bytesTotal: totalBytes,
          message: errorMessage,
          error: errorMessage,
        },
        true,
      );
      reject(new Error(errorMessage));
    });

    xhr.addEventListener("abort", () => {
      const errorMessage = "Upload was canceled before it finished.";
      emitProgress(progressUpdate({
        phase: "error",
        bytesSent: 0,
        bytesTotal: totalBytes,
        percent: 0,
        message: errorMessage,
        error: errorMessage,
      }));
      sendProgress(
        {
          phase: "error",
          bytesSent: 0,
          bytesTotal: totalBytes,
          message: errorMessage,
          error: errorMessage,
        },
        true,
      );
      reject(new Error(errorMessage));
    });

    xhr.send(formData);
  });
}

function createInfoCard(config) {
  const card = document.createElement("article");
  card.className = "info-card";

  const eyebrow = document.createElement("p");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = config.eyebrow;

  const title = document.createElement("h3");
  title.textContent = config.title;

  card.appendChild(eyebrow);
  card.appendChild(title);

  if (config.copy) {
    const copy = document.createElement("p");
    copy.className = "info-copy";
    copy.textContent = config.copy;
    card.appendChild(copy);
  }

  if (config.rows && config.rows.length) {
    const list = document.createElement("dl");
    list.className = "info-list";

    for (const row of config.rows) {
      const label = row.label == null ? "" : String(row.label).trim();
      const value = row.value == null ? "" : String(row.value).trim();
      if (!label || !value) {
        continue;
      }

      const term = document.createElement("dt");
      term.textContent = label;
      const detail = document.createElement("dd");
      detail.textContent = value;
      list.appendChild(term);
      list.appendChild(detail);
    }

    if (list.childElementCount) {
      card.appendChild(list);
    }
  }

  if (config.actions && config.actions.length) {
    const actions = document.createElement("div");
    actions.className = "info-actions";

    for (const action of config.actions) {
      actions.appendChild(createButton(action.label, action.className, action.onClick));
    }

    card.appendChild(actions);
  }

  return card;
}

function createDeviceConfigCard() {
  const status = state.status || {};
  const storedDraft = state.deviceConfigDraft || {};
  const initialDeviceName = String(storedDraft.deviceName || status.device || appDisplayName()).trim();
  const initialHotspotSsid = String(storedDraft.hotspotSsid || status.hotspotSsid || hotspotNetworkName(status) || appNetworkName()).trim();
  const initialWifiPassword = String(storedDraft.wifiPassword || status.hotspotPassword || "").trim();
  const initialTmdbApiKey = String(storedDraft.tmdbApiKey || "").trim();
  const card = document.createElement("article");
  card.className = "info-card info-card--config";

  const eyebrow = document.createElement("p");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = "Config";

  const title = document.createElement("h3");
  title.textContent = "Device Settings";

  const copy = document.createElement("p");
  copy.className = "info-copy";
  copy.textContent =
    "Choose the name shown in the web app, the fallback Wi-Fi details the Pi will use when it cannot join a known network, and the TMDb API key used for online metadata refreshes.";

  const form = document.createElement("form");
  form.className = "upload-form";

  const fields = document.createElement("div");
  fields.className = "upload-grid";

  const deviceNameField = document.createElement("label");
  deviceNameField.className = "upload-field";
  const deviceNameLabel = document.createElement("span");
  deviceNameLabel.className = "upload-label";
  deviceNameLabel.textContent = "Server name";
  const deviceNameInput = document.createElement("input");
  deviceNameInput.className = "upload-text";
  deviceNameInput.type = "text";
  deviceNameInput.name = "device-name";
  deviceNameInput.maxLength = 80;
  deviceNameInput.value = initialDeviceName;
  deviceNameField.appendChild(deviceNameLabel);
  deviceNameField.appendChild(deviceNameInput);

  const hotspotNameField = document.createElement("label");
  hotspotNameField.className = "upload-field";
  const hotspotNameLabel = document.createElement("span");
  hotspotNameLabel.className = "upload-label";
  hotspotNameLabel.textContent = "Fallback Wi-Fi name";
  const hotspotNameInput = document.createElement("input");
  hotspotNameInput.className = "upload-text";
  hotspotNameInput.type = "text";
  hotspotNameInput.name = "hotspot-ssid";
  hotspotNameInput.maxLength = 32;
  hotspotNameInput.value = initialHotspotSsid;
  hotspotNameField.appendChild(hotspotNameLabel);
  hotspotNameField.appendChild(hotspotNameInput);

  const wifiPasswordField = document.createElement("label");
  wifiPasswordField.className = "upload-field upload-field--full";
  const wifiPasswordLabel = document.createElement("span");
  wifiPasswordLabel.className = "upload-label";
  wifiPasswordLabel.textContent = "Fallback Wi-Fi password";
  const wifiPasswordInput = document.createElement("input");
  wifiPasswordInput.className = "upload-text";
  wifiPasswordInput.type = "text";
  wifiPasswordInput.name = "hotspot-password";
  wifiPasswordInput.autocomplete = "new-password";
  wifiPasswordInput.minLength = 8;
  wifiPasswordInput.maxLength = 63;
  wifiPasswordInput.value = initialWifiPassword;
  wifiPasswordField.appendChild(wifiPasswordLabel);
  wifiPasswordField.appendChild(wifiPasswordInput);

  const tmdbApiKeyField = document.createElement("label");
  tmdbApiKeyField.className = "upload-field";
  const tmdbApiKeyLabel = document.createElement("span");
  tmdbApiKeyLabel.className = "upload-label";
  tmdbApiKeyLabel.textContent = "TMDb API key";
  const tmdbApiKeyInput = document.createElement("input");
  tmdbApiKeyInput.className = "upload-text";
  tmdbApiKeyInput.type = "password";
  tmdbApiKeyInput.name = "tmdb-api-key";
  tmdbApiKeyInput.autocomplete = "off";
  tmdbApiKeyInput.value = initialTmdbApiKey;
  tmdbApiKeyField.appendChild(tmdbApiKeyLabel);
  tmdbApiKeyField.appendChild(tmdbApiKeyInput);

  const note = document.createElement("p");
  note.className = "upload-note";
  note.textContent =
    "These values are saved on the Pi. Fallback Wi-Fi changes take effect the next time hotspot mode starts, and the TMDb API key is used on the next online rescan.";

  const feedback = document.createElement("p");
  feedback.className = "upload-status";

  const actions = document.createElement("div");
  actions.className = "upload-actions";
  const submit = document.createElement("button");
  submit.type = "submit";
  submit.className = "primary-button";
  submit.textContent = "Save Settings";
  actions.appendChild(submit);

  fields.appendChild(deviceNameField);
  fields.appendChild(hotspotNameField);
  fields.appendChild(wifiPasswordField);
  fields.appendChild(tmdbApiKeyField);
  form.appendChild(fields);
  form.appendChild(note);
  form.appendChild(feedback);
  form.appendChild(actions);

  const applyConfigState = (message, tone) => {
    feedback.textContent = message || "";
    feedback.className = "upload-status";
    if (tone) {
      feedback.classList.add(`upload-status--${tone}`);
    }
  };

  const syncDraft = () => {
    state.deviceConfigDraft = {
      deviceName: deviceNameInput.value.trim(),
      hotspotSsid: hotspotNameInput.value.trim(),
      wifiPassword: wifiPasswordInput.value.trim(),
      tmdbApiKey: tmdbApiKeyInput.value.trim(),
    };
  };

  deviceNameInput.addEventListener("input", syncDraft);
  hotspotNameInput.addEventListener("input", syncDraft);
  wifiPasswordInput.addEventListener("input", syncDraft);
  tmdbApiKeyInput.addEventListener("input", syncDraft);
  syncDraft();
  applyConfigState(state.deviceConfigFeedback, state.deviceConfigFeedbackTone);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    syncDraft();
    const draft = {
      deviceName: String(state.deviceConfigDraft.deviceName || "").trim(),
      hotspotSsid: String(state.deviceConfigDraft.hotspotSsid || "").trim(),
      wifiPassword: String(state.deviceConfigDraft.wifiPassword || "").trim(),
      tmdbApiKey: String(state.deviceConfigDraft.tmdbApiKey || "").trim(),
    };

    if (!draft.deviceName) {
      state.deviceConfigFeedback = "Enter a server name.";
      state.deviceConfigFeedbackTone = "error";
      applyConfigState(state.deviceConfigFeedback, state.deviceConfigFeedbackTone);
      return;
    }
    if (!draft.hotspotSsid) {
      state.deviceConfigFeedback = "Enter a fallback Wi-Fi name.";
      state.deviceConfigFeedbackTone = "error";
      applyConfigState(state.deviceConfigFeedback, state.deviceConfigFeedbackTone);
      return;
    }
    if (draft.wifiPassword.length < 8 || draft.wifiPassword.length > 63) {
      state.deviceConfigFeedback = "Fallback Wi-Fi password must be 8-63 characters.";
      state.deviceConfigFeedbackTone = "error";
      applyConfigState(state.deviceConfigFeedback, state.deviceConfigFeedbackTone);
      return;
    }

    const previousHotspotSsid = String(status.hotspotSsid || "").trim();
    const previousWifiPassword = String(status.hotspotPassword || "").trim();
    form.querySelectorAll("input, button").forEach((element) => {
      element.disabled = true;
    });
    applyConfigState("Saving device settings on the Pi...", "pending");

    try {
      const payload = await saveDeviceConfig(draft);
      const savedConfig = payload.config || draft;
      state.deviceConfigDraft = {
        deviceName: String(savedConfig.deviceName || draft.deviceName).trim(),
        hotspotSsid: String(savedConfig.hotspotSsid || draft.hotspotSsid).trim(),
        wifiPassword: String(savedConfig.wifiPassword || draft.wifiPassword).trim(),
        tmdbApiKey: String(savedConfig.tmdbApiKey || draft.tmdbApiKey).trim(),
      };
      state.deviceConfigLoaded = true;
      if (payload.status) {
        state.status = payload.status;
        refreshLiveUploadActivity(uploadStatusSnapshot());
        refreshLiveMetadataActivity(metadataRefreshSnapshot());
      }
      const wifiChanged =
        state.deviceConfigDraft.hotspotSsid !== previousHotspotSsid ||
        state.deviceConfigDraft.wifiPassword !== previousWifiPassword;
      state.deviceConfigFeedback = payload.message || "Saved device settings.";
      if (wifiChanged) {
        state.deviceConfigFeedback = `${state.deviceConfigFeedback} If the hotspot is already live, reconnect after it restarts or reboot the Pi.`;
      }
      state.deviceConfigFeedbackTone = "success";
      render();
      els.pageSubtitle.textContent = state.deviceConfigFeedback;
    } catch (error) {
      state.deviceConfigFeedback = error.message || "Unable to save device settings.";
      state.deviceConfigFeedbackTone = "error";
      applyConfigState(state.deviceConfigFeedback, state.deviceConfigFeedbackTone);
      form.querySelectorAll("input, button").forEach((element) => {
        element.disabled = false;
      });
    }
  });

  card.appendChild(eyebrow);
  card.appendChild(title);
  card.appendChild(copy);
  card.appendChild(form);
  return card;
}

function createMetadataRefreshCard() {
  const status = metadataRefreshSnapshot();
  const card = document.createElement("article");
  card.className = "info-card info-card--metadata";

  const eyebrow = document.createElement("p");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = "Metadata";

  const title = document.createElement("h3");
  title.textContent = "Metadata Refresh Output";

  const copy = document.createElement("p");
  copy.className = "info-copy";
  copy.textContent =
    "Refresh library details here and follow along while the Pi updates posters, summaries, and ratings. The live output stays visible below the whole time.";

  const actions = document.createElement("div");
  actions.className = "info-actions";
  actions.appendChild(
    createButton("Rescan Library", "primary-button", () => {
      rescanLibrary().catch((error) => {
        els.pageSubtitle.textContent = `Rescan failed: ${error.message}`;
      });
    }),
  );

  const activity = document.createElement("div");
  registerLiveMetadataActivityTarget(activity);
  renderMetadataRefreshActivity(activity, status);

  card.appendChild(eyebrow);
  card.appendChild(title);
  card.appendChild(copy);
  card.appendChild(actions);
  card.appendChild(activity);
  return card;
}

function createUploadCard() {
  const draft = state.uploadDraft || {};
  const initialDestination = normalizeUploadDestinationPath(draft.destination) || defaultUploadDestination();
  let selectedDestination = initialDestination;
  const config = uploadRootConfigForPath(initialDestination);
  const sharedUpload = uploadStatusSnapshot();
  const card = document.createElement("article");
  card.className = "info-card info-card--upload";

  const eyebrow = document.createElement("p");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = "Upload";

  const title = document.createElement("h3");
  title.textContent = "Add Media Over Wi-Fi";

  const copy = document.createElement("p");
  copy.className = "info-copy";
  copy.textContent =
    "Browse the media tree, click the folder you want, and upload straight into it. You can still add a new subfolder on top of the selected destination, or send a whole folder tree and preserve its structure.";

  const activity = document.createElement("div");
  registerLiveUploadActivityTarget(activity);
  renderUploadActivity(activity, sharedUpload);

  const form = document.createElement("form");
  form.className = "upload-form";

  const fields = document.createElement("div");
  fields.className = "upload-grid";

  const destinationField = document.createElement("div");
  destinationField.className = "upload-field upload-field--full";
  const destinationLabel = document.createElement("span");
  destinationLabel.className = "upload-label";
  destinationLabel.textContent = "Destination folder";
  const destinationShell = document.createElement("div");
  destinationShell.className = "upload-destination-shell";
  const destinationCurrent = document.createElement("div");
  destinationCurrent.className = "upload-current-destination";
  const destinationCurrentLabel = document.createElement("span");
  destinationCurrentLabel.className = "upload-current-label";
  destinationCurrentLabel.textContent = "Selected";
  const destinationCurrentValue = document.createElement("strong");
  destinationCurrentValue.className = "upload-current-value";
  const rootRow = document.createElement("div");
  rootRow.className = "upload-root-row";
  const breadcrumbRow = document.createElement("div");
  breadcrumbRow.className = "upload-breadcrumb-row";
  const browser = document.createElement("div");
  browser.className = "upload-browser";
  const browserActions = document.createElement("div");
  browserActions.className = "upload-browser-actions";
  const browserList = document.createElement("div");
  browserList.className = "upload-browser-list";
  destinationCurrent.appendChild(destinationCurrentLabel);
  destinationCurrent.appendChild(destinationCurrentValue);
  destinationShell.appendChild(destinationCurrent);
  destinationShell.appendChild(rootRow);
  destinationShell.appendChild(breadcrumbRow);
  destinationShell.appendChild(browser);
  browser.appendChild(browserActions);
  browser.appendChild(browserList);
  destinationField.appendChild(destinationLabel);
  destinationField.appendChild(destinationShell);

  const newFolderField = document.createElement("label");
  newFolderField.className = "upload-field";
  const newFolderLabel = document.createElement("span");
  newFolderLabel.className = "upload-label";
  newFolderLabel.textContent = "New subfolder (optional)";
  const newFolderInput = document.createElement("input");
  newFolderInput.className = "upload-text";
  newFolderInput.type = "text";
  newFolderInput.name = "upload-new-folder";
  newFolderInput.value = draft.newFolder || "";
  newFolderInput.placeholder = config.newFolderPlaceholder;
  newFolderField.appendChild(newFolderLabel);
  newFolderField.appendChild(newFolderInput);

  const fileField = document.createElement("label");
  fileField.className = "upload-field";
  const fileLabel = document.createElement("span");
  fileLabel.className = "upload-label";
  fileLabel.textContent = "Loose files";
  const fileInput = document.createElement("input");
  fileInput.className = "upload-file";
  fileInput.type = "file";
  fileInput.name = "upload-files";
  fileInput.multiple = true;
  fileField.appendChild(fileLabel);
  fileField.appendChild(fileInput);

  const folderField = document.createElement("label");
  folderField.className = "upload-field";
  const folderLabel = document.createElement("span");
  folderLabel.className = "upload-label";
  folderLabel.textContent = "Whole folder";
  const folderInput = document.createElement("input");
  folderInput.className = "upload-file";
  folderInput.type = "file";
  folderInput.name = "upload-folder-tree";
  folderInput.multiple = true;
  folderInput.setAttribute("webkitdirectory", "");
  folderInput.setAttribute("directory", "");
  folderField.appendChild(folderLabel);
  folderField.appendChild(folderInput);

  const note = document.createElement("p");
  note.className = "upload-note";

  const feedback = document.createElement("p");
  feedback.className = "upload-status";

  const actions = document.createElement("div");
  actions.className = "upload-actions";
  const submit = document.createElement("button");
  submit.type = "submit";
  submit.className = "primary-button";
  submit.textContent = "Upload And Rescan";
  actions.appendChild(submit);

  fields.appendChild(destinationField);
  fields.appendChild(newFolderField);
  fields.appendChild(fileField);
  fields.appendChild(folderField);
  form.appendChild(fields);
  form.appendChild(note);
  form.appendChild(feedback);
  form.appendChild(actions);

  const applyUploadState = (message, tone) => {
    feedback.textContent = message || "";
    feedback.className = "upload-status";
    if (tone) {
      feedback.classList.add(`upload-status--${tone}`);
    }
  };

  const syncSelectionLock = () => {
    state.uploadSelectionLocked = Boolean(
      (fileInput.files && fileInput.files.length) ||
      (folderInput.files && folderInput.files.length) ||
      state.uploadingLocally,
    );
  };

  const renderDestinationPicker = () => {
    const activeConfig = uploadRootConfigForPath(selectedDestination);
    destinationCurrentValue.textContent = selectedDestination;

    rootRow.innerHTML = "";
    for (const root of UPLOAD_ROOTS) {
      const button = createButton(root.label, "ghost-button upload-root-button", () => {
        selectedDestination = root.root;
        state.uploadDraft.destination = selectedDestination;
        renderDestinationPicker();
        syncHints();
      });
      if (selectedDestination === root.root || selectedDestination.startsWith(`${root.root}/`)) {
        button.classList.add("is-active");
      }
      rootRow.appendChild(button);
    }

    breadcrumbRow.innerHTML = "";
    const breadcrumbs = uploadDestinationBreadcrumbs(selectedDestination);
    const nestedBreadcrumbs = breadcrumbs.slice(1);
    breadcrumbRow.hidden = nestedBreadcrumbs.length === 0;
    for (const crumb of nestedBreadcrumbs) {
      const button = createButton(crumb.label, "ghost-button upload-breadcrumb-button", () => {
        selectedDestination = crumb.path;
        state.uploadDraft.destination = selectedDestination;
        renderDestinationPicker();
        syncHints();
      });
      if (crumb.path === selectedDestination) {
        button.classList.add("is-active");
      }
      breadcrumbRow.appendChild(button);
    }

    browserActions.innerHTML = "";
    const parentPath = uploadParentDestination(selectedDestination);
    if (parentPath) {
      browserActions.appendChild(
        createButton(`Up To ${uploadDestinationTitle(parentPath)}`, "ghost-button upload-nav-button", () => {
          selectedDestination = parentPath;
          state.uploadDraft.destination = selectedDestination;
          renderDestinationPicker();
          syncHints();
        }),
      );
    } else {
      const helper = document.createElement("p");
      helper.className = "upload-browser-helper";
      helper.textContent = `Browsing ${activeConfig.label}`;
      browserActions.appendChild(helper);
    }

    browserList.innerHTML = "";
    const childDestinations = uploadChildDestinations(selectedDestination);
    if (!childDestinations.length) {
      const empty = document.createElement("p");
      empty.className = "upload-browser-empty";
      empty.textContent = "No deeper folders here yet. Use New subfolder to create one during upload.";
      browserList.appendChild(empty);
      return;
    }

    for (const childPath of childDestinations) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "upload-folder-button";
      const titleText = document.createElement("strong");
      titleText.className = "upload-folder-button-title";
      titleText.textContent = uploadDestinationTitle(childPath);
      const metaText = document.createElement("span");
      metaText.className = "upload-folder-button-meta";
      metaText.textContent = childPath;
      button.appendChild(titleText);
      button.appendChild(metaText);
      button.addEventListener("click", () => {
        selectedDestination = childPath;
        state.uploadDraft.destination = selectedDestination;
        renderDestinationPicker();
        syncHints();
      });
      browserList.appendChild(button);
    }
  };

  const syncHints = () => {
    const activeConfig = uploadRootConfigForPath(selectedDestination || initialDestination);
    const finalDestination = uploadDestinationPreview(selectedDestination, newFolderInput.value);
    const selectedSummary = describeUploadSelection(fileInput.files, folderInput.files);
    const actualMediaRoot = (state.status && state.status.mediaRoot) || "";
    newFolderInput.placeholder = activeConfig.newFolderPlaceholder;
    note.textContent = `${uploadDestinationHelp(selectedDestination)} Selected folder: ${selectedDestination}. Final destination: ${finalDestination || selectedDestination}.${actualMediaRoot ? ` Files are stored on the Pi under ${actualMediaRoot}.` : ""}${selectedSummary ? ` Selected: ${selectedSummary}.` : " Select loose files, a whole folder, or both."}`;
    state.uploadDraft.destination = selectedDestination;
    state.uploadDraft.newFolder = newFolderInput.value.trim();
  };

  const handleSelectionChange = () => {
    syncSelectionLock();
    syncHints();
  };

  newFolderInput.addEventListener("input", syncHints);
  fileInput.addEventListener("change", handleSelectionChange);
  folderInput.addEventListener("change", handleSelectionChange);
  renderDestinationPicker();
  syncSelectionLock();
  syncHints();
  applyUploadState(state.uploadFeedback, state.uploadFeedbackTone);

  const applySharedUploadPreview = (upload) => {
    if (state.status) {
      state.status.upload = {
        ...((state.status && state.status.upload) || {}),
        ...upload,
      };
    }
    refreshLiveUploadActivity(upload);
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    syncSelectionLock();
    const entries = collectUploadEntries(fileInput.files, folderInput.files);
    if (!entries.length) {
      state.uploadFeedback = "Choose at least one file or folder to upload.";
      state.uploadFeedbackTone = "error";
      applyUploadState(state.uploadFeedback, state.uploadFeedbackTone);
      return;
    }

    const destination = buildUploadDestination(selectedDestination, newFolderInput.value);
    if (!destination) {
      state.uploadFeedback = "Pick a destination under /media before uploading.";
      state.uploadFeedbackTone = "error";
      applyUploadState(state.uploadFeedback, state.uploadFeedbackTone);
      return;
    }

    state.uploadDraft.destination = selectedDestination;
    state.uploadDraft.newFolder = newFolderInput.value.trim();
    state.uploadingLocally = true;
    syncSelectionLock();

    form.querySelectorAll("input, button").forEach((element) => {
      element.disabled = true;
    });
    applyUploadState(`Uploading ${entries.length} file${entries.length === 1 ? "" : "s"} to ${destination}...`, "pending");
    els.pageSubtitle.textContent = `Uploading files to ${destination}...`;

    try {
      const payload = await uploadLibraryFiles(destination, entries, {
        onProgress: (progress) => {
          applySharedUploadPreview(progress);
          applyUploadState(progress.message || "Uploading files...", "pending");
          els.pageSubtitle.textContent = progress.message || "Uploading files...";
        },
      });
      const warningText =
        Array.isArray(payload.warnings) && payload.warnings.length ? ` ${payload.warnings.join(" ")}` : "";
      const savedBytesText = Number(payload.savedBytes || 0) > 0 ? ` Saved ${formatBytes(payload.savedBytes)}.` : "";
      const mediaRootText = payload.mediaRoot ? ` Stored under ${payload.mediaRoot}.` : "";
      state.uploadFeedback = `Uploaded ${payload.count} file${payload.count === 1 ? "" : "s"} to ${payload.destination || destination}. The library has been rescanned.${savedBytesText}${mediaRootText}${warningText}`;
      state.uploadFeedbackTone = Array.isArray(payload.warnings) && payload.warnings.length ? "pending" : "success";
      state.preferServerLibrary = true;
      if (payload.upload) {
        applySharedUploadPreview(payload.upload);
      }
      fileInput.value = "";
      folderInput.value = "";
      state.uploadSelectionLocked = false;
      els.pageSubtitle.textContent = state.uploadFeedback;
      state.uploadingLocally = false;
      syncHints();
      try {
        await refreshAllWithRetry({ attempts: 4, delayMs: 700 });
      } catch (refreshError) {
        console.warn("Upload completed but the post-upload refresh failed", refreshError);
        state.uploadFeedback = `${state.uploadFeedback} The files are on the Pi, but this browser could not refresh the library view yet. Try Refresh Device Data in a moment.`;
        state.uploadFeedbackTone = "pending";
        els.pageSubtitle.textContent = state.uploadFeedback;
        applyUploadState(state.uploadFeedback, state.uploadFeedbackTone);
        form.querySelectorAll("input, select, button").forEach((element) => {
          element.disabled = false;
        });
      }
    } catch (error) {
      state.uploadFeedback = error.message || "Upload failed.";
      state.uploadFeedbackTone = "error";
      els.pageSubtitle.textContent = `Upload failed: ${state.uploadFeedback}`;
      applyUploadState(state.uploadFeedback, state.uploadFeedbackTone);
      applySharedUploadPreview({
        ...(uploadStatusSnapshot() || {}),
        active: false,
        phase: "error",
        destination,
        message: state.uploadFeedback,
        error: state.uploadFeedback,
      });
      state.uploadingLocally = false;
      syncSelectionLock();
      form.querySelectorAll("input, button").forEach((element) => {
        element.disabled = false;
      });
    }
  });

  card.appendChild(eyebrow);
  card.appendChild(title);
  card.appendChild(copy);
  card.appendChild(activity);
  card.appendChild(form);
  return card;
}

function renderBreadcrumbs(show, movie, season, audiobook, documentBrowser) {
  const crumbs = [{ label: "Home", href: "/app" }];
  const audiobookBrowseTarget = currentAudiobookBrowseTarget();

  if (state.route.name === "movies") {
    crumbs.push({ label: "Movies", href: "/app/movies" });
  } else if (state.route.name === "movie") {
    crumbs.push({ label: "Movies", href: "/app/movies" });
    crumbs.push({ label: movie ? movie.title : titleFromPath(state.route.path || "") || "Movie" });
  } else if (state.route.name === "tv") {
    crumbs.push({ label: "TV Shows", href: "/app/tv" });
  } else if (state.route.name === "show") {
    crumbs.push({ label: "TV Shows", href: "/app/tv" });
    crumbs.push({ label: show ? show.title : "Show" });
  } else if (state.route.name === "season") {
    crumbs.push({ label: "TV Shows", href: "/app/tv" });
    crumbs.push({ label: show ? show.title : "Show", href: show ? buildRoutePath({ name: "show", slug: show.slug }) : "/app/tv" });
    crumbs.push({ label: season ? season.label : "Season" });
  } else if (state.route.name === "music") {
    crumbs.push({ label: "Music", href: "/app/music" });
  } else if (state.route.name === "audiobooks") {
    crumbs.push({ label: "Audiobooks", href: "/app/audiobooks" });
  } else if (state.route.name === "audiobookCollection") {
    crumbs.push({ label: "Audiobooks", href: "/app/audiobooks" });
    crumbs.push({ label: "Collections" });
    crumbs.push({ label: audiobookBrowseTarget.label || "Collection" });
  } else if (state.route.name === "audiobookAuthor") {
    crumbs.push({ label: "Audiobooks", href: "/app/audiobooks" });
    crumbs.push({ label: "Authors" });
    crumbs.push({ label: audiobookBrowseTarget.label || "Author" });
  } else if (state.route.name === "audiobook") {
    crumbs.push({ label: "Audiobooks", href: "/app/audiobooks" });
    crumbs.push({ label: audiobook ? audiobook.title : titleFromPath(state.route.path || "") || "Audiobook" });
  } else if (state.route.name === "documents") {
    crumbs.push({ label: "Documents", href: "/app/documents" });
    const segments = documentFolderSegments(state.route.folder);
    let folderPath = "";
    for (let index = 0; index < segments.length; index += 1) {
      folderPath = normalizeDocumentFolder(folderPath ? `${folderPath}/${segments[index]}` : segments[index]);
      crumbs.push(
        index < segments.length - 1
          ? { label: segments[index], href: buildRoutePath(documentRoute(folderPath)) }
          : {
              label:
                documentBrowser && !documentBrowser.exists
                  ? `${segments[index]} (missing)`
                  : segments[index],
            },
      );
    }
  } else if (state.route.name === "device") {
    crumbs.push({ label: "Device Info", href: "/app/device" });
  }

  els.breadcrumbs.innerHTML = "";

  for (let index = 0; index < crumbs.length; index += 1) {
    const crumb = crumbs[index];

    if (crumb.href) {
      const link = document.createElement("a");
      link.href = crumb.href;
      link.dataset.route = "true";
      link.textContent = crumb.label;
      els.breadcrumbs.appendChild(link);
    } else {
      const current = document.createElement("span");
      current.textContent = crumb.label;
      els.breadcrumbs.appendChild(current);
    }

    if (index < crumbs.length - 1) {
      const divider = document.createElement("span");
      divider.textContent = "/";
      els.breadcrumbs.appendChild(divider);
    }
  }
}

function counts() {
  return (state.catalogSummary && state.catalogSummary.counts) || (state.library && state.library.counts) || {
    total: 0,
    movies: 0,
    shows: 0,
    episodes: 0,
    music: 0,
    audiobooks: 0,
    documents: 0,
  };
}

function updatePageHeader(show, movie, season, audiobook, documentBrowser) {
  const summary = counts();
  const audiobookBrowseTarget = currentAudiobookBrowseTarget();
  const audiobookPage = state.catalogAudiobooks || createEmptyCatalogPageState();
  let meta = {
    eyebrow: "Home",
    title: "",
    subtitle: homeSummaryText(summary),
    searchPlaceholder: "Search the whole library",
  };

  if (state.route.name === "movies") {
    meta = {
      eyebrow: "Movies",
      title: "Movie library",
      subtitle: `${summary.movies} movie${summary.movies === 1 ? "" : "s"} ready to stream with local metadata when available.`,
      searchPlaceholder: "Search movies",
    };
  } else if (state.route.name === "movie") {
    meta = movie
      ? {
          eyebrow: "Movie Detail",
          title: titleWithYear(movie.title, movie.year),
          subtitle:
            truncateText(movie.overview, 180) ||
            truncateText(movie.tagline, 180) ||
            `Open this title for artwork, file details, and local metadata.`,
          searchPlaceholder: "Search movie details",
        }
      : {
          eyebrow: "Movie Detail",
          title: state.catalogMovieLoaded ? "Movie not found" : titleFromPath(state.route.path || "") || "Loading movie",
          subtitle: state.catalogMovieLoaded
            ? "This movie is not in the current media scan."
            : "Loading movie details from the library...",
          searchPlaceholder: "Search movie details",
        };
  } else if (state.route.name === "tv") {
    meta = {
      eyebrow: "TV Shows",
      title: "Series library",
      subtitle: `${summary.shows} show${summary.shows === 1 ? "" : "s"} and ${summary.episodes} episodes grouped by season.`,
      searchPlaceholder: "Search shows",
    };
  } else if (state.route.name === "show") {
    meta = show
      ? {
          eyebrow: "Show Detail",
          title: show.title,
          subtitle:
            truncateText(show.overview, 180) ||
            `${show.seasonCount} season${show.seasonCount === 1 ? "" : "s"} ready to open before you drill into episodes.`,
          searchPlaceholder: "Search seasons",
        }
      : {
          eyebrow: "Show Detail",
          title: state.catalogShowLoaded ? "Show not found" : "Loading show",
          subtitle: state.catalogShowLoaded
            ? "This show is not in the current media scan."
            : "Loading show details from the media library...",
          searchPlaceholder: "Search seasons",
        };
  } else if (state.route.name === "season") {
    meta = season && show
      ? {
          eyebrow: "Season Detail",
          title: `${show.title} | ${season.label}`,
          subtitle: `${season.episodeCount || (season.episodes || []).length} episode${(season.episodeCount || (season.episodes || []).length) === 1 ? "" : "s"} ready to play in card view.`,
          searchPlaceholder: "Search episodes",
        }
      : {
          eyebrow: "Season Detail",
          title: state.catalogShowLoaded ? "Season not found" : "Loading season",
          subtitle: state.catalogShowLoaded
            ? "This season is not in the current media scan."
            : "Loading season details from the media library...",
          searchPlaceholder: "Search episodes",
        };
  } else if (state.route.name === "music") {
    meta = {
      eyebrow: "Music",
      title: "Track library",
      subtitle: `${summary.music} track${summary.music === 1 ? "" : "s"} ready for offline listening.`,
      searchPlaceholder: "Search tracks",
    };
  } else if (state.route.name === "audiobooks") {
    meta = {
      eyebrow: "Audiobooks",
      title: "Audiobook library",
      subtitle: `${summary.audiobooks} audiobook${summary.audiobooks === 1 ? "" : "s"} ready for offline listening.`,
      searchPlaceholder: "Search audiobooks",
    };
  } else if (state.route.name === "audiobookCollection") {
    meta = {
      eyebrow: "Collection",
      title: audiobookBrowseTarget.label || "Collection",
      subtitle: audiobookPage.loaded
        ? `${audiobookPage.total} audiobook${audiobookPage.total === 1 ? "" : "s"} in this collection.`
        : "Loading this audiobook collection from the library...",
      searchPlaceholder: "Search this collection",
    };
  } else if (state.route.name === "audiobookAuthor") {
    meta = {
      eyebrow: "Author",
      title: audiobookBrowseTarget.label || "Author",
      subtitle: audiobookPage.loaded
        ? `${audiobookPage.total} audiobook${audiobookPage.total === 1 ? "" : "s"} by this author.`
        : "Loading this audiobook author from the library...",
      searchPlaceholder: "Search this author",
    };
  } else if (state.route.name === "audiobook") {
    meta = audiobook
      ? {
          eyebrow: "Audiobook Detail",
          title: titleWithYear(audiobook.title, audiobook.year),
          subtitle:
            truncateText(audiobook.overview, 180) ||
            truncateText(itemSummary(audiobook), 180) ||
            "Open this audiobook for artwork, narrators, publisher, runtime, and file details.",
          searchPlaceholder: "Search audiobook details",
        }
      : {
          eyebrow: "Audiobook Detail",
          title: state.catalogAudiobookLoaded ? "Audiobook not found" : titleFromPath(state.route.path || "") || "Loading audiobook",
          subtitle: state.catalogAudiobookLoaded
            ? "This audiobook is not in the current media scan."
            : "Loading audiobook details from the library...",
          searchPlaceholder: "Search audiobook details",
        };
  } else if (state.route.name === "documents") {
    meta =
      documentBrowser && documentBrowser.currentFolder
        ? documentBrowser.exists
          ? {
              eyebrow: "Documents",
              title: documentBrowser.title,
              subtitle: `${countLabel(documentBrowser.totalFolders, "folder")} and ${countLabel(documentBrowser.totalFiles, "file")} in this location.`,
              searchPlaceholder: "Search this folder",
            }
          : {
              eyebrow: "Documents",
              title: documentBrowser.title,
              subtitle: "This folder is not in the current media index.",
              searchPlaceholder: "Search documents",
            }
        : {
            eyebrow: "Documents",
            title: "Field files",
            subtitle: `${summary.documents} file${summary.documents === 1 ? "" : "s"} including PDFs, maps, permits, and images.`,
            searchPlaceholder: "Search documents",
          };
  } else if (state.route.name === "device") {
    const status = state.status;
    const deviceLabel = status && status.device ? status.device : appDisplayName();
    meta = {
      eyebrow: "Device Info",
      title: deviceLabel,
      subtitle: "Wi-Fi details, library maintenance, metadata health, and other device controls live here.",
      searchPlaceholder: "Search device details",
    };
  }

  const appTitle = appDisplayName();
  document.title = meta.title ? `${meta.title} | ${appTitle}` : appTitle;
  const isDetailPage =
    state.route.name === "movie" ||
    state.route.name === "show" ||
    state.route.name === "season" ||
    state.route.name === "audiobook";
  els.pageTools.hidden = isDetailPage;
  els.pageTools.style.display = isDetailPage ? "none" : "";
  els.pageEyebrow.hidden = isDetailPage || !meta.eyebrow;
  els.pageEyebrow.textContent = meta.eyebrow;
  els.pageTitle.hidden = isDetailPage || !meta.title;
  els.pageTitle.textContent = meta.title;
  els.pageSubtitle.hidden = isDetailPage || !meta.subtitle;
  els.pageSubtitle.textContent = meta.subtitle;
  els.search.placeholder = meta.searchPlaceholder;
}

function updatePageActions(show, movie, season, documentBrowser) {
  els.actions.innerHTML = "";

  if (state.route.name === "home") {
    els.actions.appendChild(
      createButton("Play Something", "primary-button", () => {
        loadRandomCatalogItem()
          .then((item) => {
            if (item) {
              playItem(item);
            }
          })
          .catch((error) => {
            els.pageSubtitle.textContent = `Random playback failed: ${error.message}`;
          });
      }),
    );
    els.actions.appendChild(
      createButton("Browse Movies", "ghost-button", () => openRoute({ name: "movies" })),
    );
    return;
  }

  if (state.route.name === "movies") {
    els.actions.appendChild(
      createButton("Shuffle Movie", "primary-button", () => {
        loadRandomCatalogItem("movies")
          .then((item) => {
            if (item) {
              playItem(item);
            }
          })
          .catch((error) => {
            els.pageSubtitle.textContent = `Movie shuffle failed: ${error.message}`;
          });
      }),
    );
    return;
  }

  if (
    state.route.name === "movie" ||
    state.route.name === "show" ||
    state.route.name === "season" ||
    state.route.name === "audiobook"
  ) {
    return;
  }

  if (state.route.name === "tv") {
    els.actions.appendChild(
      createButton("Browse Home", "ghost-button", () => openRoute({ name: "home" })),
    );
    return;
  }

  if (state.route.name === "show") {
    if (show && firstSeason(show)) {
      els.actions.appendChild(
        createButton("Open First Season", "primary-button", () => openRoute(seasonRoute(show, firstSeason(show)))),
      );
    }
    els.actions.appendChild(
      createButton("Back To Shows", "ghost-button", () => openRoute({ name: "tv" })),
    );
    return;
  }

  if (state.route.name === "season") {
    if (season && firstEpisodeInSeason(season)) {
      els.actions.appendChild(
        createButton("Play First Episode", "primary-button", () => playItem(firstEpisodeInSeason(season))),
      );
    }
    if (show) {
      els.actions.appendChild(
        createButton("Back To Seasons", "ghost-button", () => openRoute({ name: "show", slug: show.slug })),
      );
    }
    return;
  }

  if (state.route.name === "music") {
    els.actions.appendChild(
      createButton("Shuffle Track", "primary-button", () => {
        const items = filterMediaItems(librarySections().music);
        if (!items.length) {
          return;
        }
        const item = items[Math.floor(Math.random() * items.length)];
        playItem(item);
      }),
    );
    return;
  }

  if (state.route.name === "audiobooks") {
    els.actions.appendChild(
      createButton("Shuffle Audiobook", "primary-button", () => {
        loadRandomCatalogItem("audiobooks")
          .then((item) => {
            if (item) {
              playItem(item);
            }
          })
          .catch((error) => {
            els.pageSubtitle.textContent = `Audiobook shuffle failed: ${error.message}`;
          });
      }),
    );
    return;
  }

  if (state.route.name === "audiobookCollection" || state.route.name === "audiobookAuthor") {
    els.actions.appendChild(
      createButton("Back To Audiobooks", "ghost-button", () => openRoute({ name: "audiobooks" })),
    );
    return;
  }

  if (state.route.name === "documents") {
    if (documentBrowser && documentBrowser.currentFolder) {
      els.actions.appendChild(
        createButton("Up One Level", "primary-button", () => openRoute(documentRoute(documentBrowser.parentFolder))),
      );
      els.actions.appendChild(
        createButton("Open Root", "ghost-button", () => openRoute(documentRoute(""))),
      );
    } else {
      els.actions.appendChild(
        createButton("Open Home", "ghost-button", () => openRoute({ name: "home" })),
      );
    }
    return;
  }

  if (state.route.name === "device") {
    els.actions.appendChild(
      createButton("Refresh Device Data", "ghost-button", () => {
        refreshDeviceData().catch((error) => {
          els.pageSubtitle.textContent = `Refresh failed: ${error.message}`;
        });
      }),
    );
    els.actions.appendChild(
      createButton("Open Home", "ghost-button", () => openRoute({ name: "home" })),
    );
  }
}

function renderHero(show, movie, season, documentBrowser) {
  const sections = librarySections();
  els.hero.innerHTML = "";

  if (
    state.route.name === "home" ||
    state.route.name === "movie" ||
    state.route.name === "audiobook" ||
    state.route.name === "show" ||
    state.route.name === "season"
  ) {
    return;
  }

  const hero = document.createElement("section");
  hero.className = "hero-stage hero-stage--single";

  const copy = document.createElement("div");
  copy.className = "hero-copy";

  const eyebrow = document.createElement("p");
  eyebrow.className = "eyebrow";
  const title = document.createElement("h3");
  title.hidden = false;
  const subtitle = document.createElement("p");
  subtitle.className = "hero-subtitle";
  subtitle.hidden = false;

  const actionRow = document.createElement("div");
  actionRow.className = "hero-actions";

  let artUrl = "";
  let gradientKey = "nomad-screen";

  if (state.route.name === "movies") {
    const featured = (state.catalogMovies.items || [])[0] || null;
    eyebrow.textContent = "Movie Shelf";
    title.textContent = featured ? titleWithYear(featured.title, featured.year) : "Movie library";
    subtitle.textContent = featured
      ? itemSummary(featured)
      : "Load movies into /media/movies and refresh metadata to populate this shelf.";
    artUrl = featured ? featured.backdropUrl || featured.posterUrl : "";
    gradientKey = featured ? featured.path : "movies";
    if (featured) {
      actionRow.appendChild(
        createButton("Play Featured Movie", "primary-button", () => openMoviePage(featured, { autoplay: true })),
      );
    }
    actionRow.appendChild(
      createButton("Jump To Grid", "ghost-button", () => {
        els.content.scrollIntoView({ behavior: "smooth", block: "start" });
      }),
    );
  } else if (state.route.name === "movie") {
    const movieResumeProgress = movie ? resumeProgressForItem(movie) : null;
    eyebrow.textContent = "Movie Detail";
    title.textContent = movie ? titleWithYear(movie.title, movie.year) : titleFromPath(state.route.path || "") || "Movie";
    subtitle.textContent = movie
      ? truncateText(movie.overview, 220) ||
        truncateText(movie.tagline, 220) ||
        "Movie description and file details are grouped below this spotlight."
      : state.catalogMovieLoaded
        ? "Rescan the library or head back to the movies page to pick another title."
        : "Loading movie details from the library...";
    artUrl = movie ? movie.backdropUrl || movie.posterUrl : "";
    gradientKey = movie ? movie.path : "movie";
    if (movie) {
      actionRow.appendChild(
        createButton(movieResumeProgress ? "Resume" : "Play Now", "primary-button", () => playItem(movie)),
      );
    }
    actionRow.appendChild(
      createButton("Back To Movies", "ghost-button", () => openRoute({ name: "movies" })),
    );
  } else if (state.route.name === "tv") {
    const featured = (state.catalogShows.items || [])[0] || null;
    eyebrow.textContent = "Series Shelf";
    title.textContent = featured ? featured.title : "TV library";
    subtitle.textContent = featured
      ? showCardSubtitle(featured)
      : "Drop shows into /media/tv/Show Name/Season 1 to populate this page.";
    artUrl = featured ? featured.backdropUrl || featured.posterUrl : "";
    gradientKey = featured ? featured.slug : "tv";
    if (featured) {
      actionRow.appendChild(
        createButton("Open Featured Show", "primary-button", () => openRoute({ name: "show", slug: featured.slug })),
      );
    }
  } else if (state.route.name === "show") {
    const first = firstSeason(show);
    eyebrow.textContent = "Show Detail";
    title.textContent = show ? show.title : "Missing show";
    subtitle.textContent = show
      ? truncateText(show.overview, 220) ||
        "Start with a season card, then open that season to browse episodes in the same card layout."
      : "Rescan the library or pick another show from the TV page.";
    artUrl = show ? show.backdropUrl || show.posterUrl : "";
    gradientKey = show ? show.slug : "show";
    if (first) {
      actionRow.appendChild(
        createButton("Open First Season", "primary-button", () => openRoute(seasonRoute(show, first))),
      );
    }
  } else if (state.route.name === "season") {
    eyebrow.textContent = "Season Detail";
    title.textContent = show && season ? `${show.title} | ${season.label}` : "Missing season";
    subtitle.textContent = season
      ? `${season.episodeCount || (season.episodes || []).length} episode${(season.episodeCount || (season.episodes || []).length) === 1 ? "" : "s"} ready to play in the same card layout as the rest of the library.`
      : state.catalogShowLoaded
        ? "Rescan the library or head back to the show page to pick another season."
        : "Loading season details from the media library...";
    artUrl = show ? show.backdropUrl || show.posterUrl : "";
    gradientKey = show ? `${show.slug}-${season ? seasonKeyForSeason(season) : "season"}` : "season";
    if (season && firstEpisodeInSeason(season)) {
      actionRow.appendChild(
        createButton("Play First Episode", "primary-button", () => playItem(firstEpisodeInSeason(season))),
      );
    }
    if (show) {
      actionRow.appendChild(
        createButton("Back To Seasons", "ghost-button", () => openRoute({ name: "show", slug: show.slug })),
      );
    }
  } else if (state.route.name === "music") {
    const featured = filterMediaItems(sections.music)[0];
    eyebrow.textContent = "Music Shelf";
    title.textContent = featured ? featured.title : "Music library";
    subtitle.textContent = featured
      ? itemSummary(featured)
      : "Load audio files into /media/music to fill this page.";
    artUrl = featured ? featured.posterUrl || featured.backdropUrl : "";
    gradientKey = featured ? featured.path : "music";
    if (featured) {
      actionRow.appendChild(
        createButton("Play Featured Track", "primary-button", () => playItem(featured)),
      );
    }
  } else if (state.route.name === "audiobooks") {
    const featured = (state.catalogAudiobooks.items || [])[0] || null;
    eyebrow.textContent = "Audiobook Shelf";
    title.textContent = featured ? featured.title : "Audiobook library";
    subtitle.textContent = featured
      ? itemSummary(featured)
      : "Load spoken audio into /media/audiobooks to fill this page.";
    artUrl = featured ? featured.posterUrl || featured.backdropUrl : "";
    gradientKey = featured ? featured.path : "audiobooks";
    if (featured) {
      actionRow.appendChild(
        createButton("Play Featured Audiobook", "primary-button", () => openAudiobookPage(featured, { autoplay: true })),
      );
    }
    actionRow.appendChild(
      createButton("Jump To Grid", "ghost-button", () => {
        els.content.scrollIntoView({ behavior: "smooth", block: "start" });
      }),
    );
  } else if (state.route.name === "audiobookCollection" || state.route.name === "audiobookAuthor") {
    const featured = (state.catalogAudiobooks.items || [])[0] || null;
    const browseTarget = currentAudiobookBrowseTarget();
    const count = Number((state.catalogAudiobooks && state.catalogAudiobooks.total) || 0);
    const kindLabel = browseTarget.kind === "collection" ? "Collection" : "Author";
    eyebrow.textContent = `Audiobook ${kindLabel}`;
    title.textContent = browseTarget.label || kindLabel;
    subtitle.textContent =
      !state.catalogAudiobooks.loaded
        ? `Loading this audiobook ${browseTarget.kind || "browse"} page from the library...`
        : count > 0
        ? `${count} audiobook${count === 1 ? "" : "s"} ${browseTarget.kind === "collection" ? "in this collection" : "by this author"}.`
        : `No audiobooks in this ${browseTarget.kind || "browse"} view match the current filters yet.`;
    artUrl = featured ? featured.posterUrl || featured.backdropUrl : "";
    gradientKey = featured ? featured.path : `audiobook-${browseTarget.kind || "browse"}`;
    if (featured) {
      actionRow.appendChild(
        createButton("Play Featured Audiobook", "primary-button", () => openAudiobookPage(featured, { autoplay: true })),
      );
    }
    actionRow.appendChild(
      createButton("Back To Audiobooks", "ghost-button", () => openRoute({ name: "audiobooks" })),
    );
  } else if (state.route.name === "documents") {
    const browser = documentBrowser || buildDocumentBrowserState(state.route.folder);
    const featured = browser.previewItem;

    if (browser.currentFolder) {
      eyebrow.textContent = "Document Folder";
      title.textContent = browser.title;
      subtitle.textContent = browser.exists
        ? `${countLabel(browser.totalFolders, "subfolder")} and ${countLabel(browser.totalFiles, "file")} are available in this folder.`
        : "This folder is not in the current media index.";
      gradientKey = browser.currentFolder;
      artUrl =
        featured && featured.type === "image"
          ? buildAssetUrl(featured.path)
          : featured
            ? featured.posterUrl || featured.backdropUrl
            : "";

      if (browser.files.length) {
        actionRow.appendChild(
          createButton(
            browser.files[0].type === "image" ? "Open First Image" : "Open First File",
            "primary-button",
            () => playItem(browser.files[0]),
          ),
        );
      }
      actionRow.appendChild(
        createButton(
          "Up One Level",
          "ghost-button",
          () => openRoute(documentRoute(browser.parentFolder)),
        ),
      );
    } else {
      eyebrow.textContent = "Document Shelf";
      title.textContent = featured ? featured.title : "Field files";
      subtitle.textContent = featured
        ? itemSummary(featured)
        : "Load PDFs, maps, permits, and images into /media/documents to fill this page.";
      artUrl =
        featured
          ? featured.type === "image"
            ? buildAssetUrl(featured.path)
            : featured.posterUrl || featured.backdropUrl
          : "";
      gradientKey = featured ? featured.path : "documents";
      if (featured) {
        actionRow.appendChild(
          createButton(
            featured.type === "image" ? "Open Featured Image" : "Open Featured File",
            "primary-button",
            () => playItem(featured),
          ),
        );
      }
    }
  } else if (state.route.name === "device") {
    const status = state.status || {};
    const preferredUrl = status.mdnsReady ? status.mdnsUrl : status.ipAppUrl || status.appUrl || "/app";
    eyebrow.textContent = "Device Control";
    title.textContent = status.device || appDisplayName();
    subtitle.textContent = status.sdMounted
      ? deviceNetworkSubtitle(status, preferredUrl)
      : "The admin page shows network details, storage health, and library maintenance controls.";
    gradientKey = "device";
    actionRow.appendChild(
      createButton("Browse Library", "ghost-button", () => openRoute({ name: "home" })),
    );
  }

  copy.style.background = createArtBackground(artUrl, gradientKey, "landscape");
  copy.appendChild(eyebrow);
  if (!title.hidden) {
    copy.appendChild(title);
  }
  copy.appendChild(subtitle);
  copy.appendChild(actionRow);
  hero.appendChild(copy);
  els.hero.appendChild(hero);
}

function renderHomePage(container) {
  const sections = homeSections();
  const results = state.query ? state.catalogSearch.items || [] : [];
  const continueEntries = continueWatchingEntries();

  if (state.query && !state.catalogSearch.loaded) {
    container.appendChild(createEmptyState("Searching the library on the Pi..."));
    return;
  }
  if (!state.query && !state.catalogHome) {
    container.appendChild(createEmptyState("Loading a lighter home shelf from the Pi..."));
    return;
  }

  if (state.query) {
    appendGridSection(container, {
      eyebrow: "Search",
      title: "Matching media",
      subtitle: `${results.length} result${results.length === 1 ? "" : "s"} from the server-side catalog search.`,
      items: results,
      renderItem: createMediaCard,
      emptyMessage: "No titles match this search yet.",
    });
    return;
  }

  appendGridSection(container, {
    eyebrow: "Continue Watching",
    title: "Resume And Next Up",
    subtitle: "Resume unfinished movies, episodes, and audiobooks, plus keep the next TV episode ready on this device profile.",
    items: continueEntries,
    renderItem: createContinueWatchingCard,
    emptyMessage: "Start a movie, episode, or audiobook on this device and it will show up here.",
  });

  appendGridSection(container, {
    eyebrow: "Movies",
    title: "Featured Movies",
    subtitle: "Posters, summaries, years, and ratings appear here when metadata is available.",
    items: sections.movies.slice(0, 8),
    renderItem: (item) =>
      createMediaCard(item, {
        compact: true,
        includeYearInTitle: true,
        cardClassName: "movie-page-card",
      }),
    emptyMessage: "Add files under /media/movies to fill this row.",
  });

  appendGridSection(container, {
    eyebrow: "TV Shows",
    title: "Series Collection",
    subtitle: "",
    items: sections.tv.slice(0, 8),
    renderItem: (show) => createShowCard(show, { compact: true }),
    emptyMessage: "Add shows under /media/tv/Show Name/Season 1.",
  });

  appendGridSection(container, {
    eyebrow: "Music",
    title: "Tracks On Deck",
    subtitle: "",
    items: sections.music.slice(0, 6),
    renderItem: (item) => createMediaCard(item, { compact: true }),
    emptyMessage: "Add audio under /media/music to see it here.",
  });

  appendGridSection(container, {
    eyebrow: "Audiobooks",
    title: "On The Shelf",
    subtitle: "",
    items: sections.audiobooks.slice(0, 6),
    renderItem: (item) => createMediaCard(item, { compact: true }),
    emptyMessage: "Add spoken audio under /media/audiobooks to see it here.",
  });

  appendGridSection(container, {
    eyebrow: "Documents",
    title: "Field Files",
    subtitle: "Browse PDFs, maps, permits, checklists, and images directly from library storage.",
    items: sections.documents.slice(0, 6),
    renderItem: createDocumentCard,
    emptyMessage: "Add files under /media/documents to see them here.",
  });
}

function renderMoviePage(container) {
  const page = state.catalogMovies;
  if (!page.loaded) {
    container.appendChild(createEmptyState("Loading a page of movies from the Pi..."));
    return;
  }

  const section = document.createElement("section");
  section.className = "content-section";
  const selectedGenre = genreFilterLabel("movies");
  const subtitle = page.total
    ? `Showing ${page.items.length} of ${page.total} movie${page.total === 1 ? "" : "s"}${selectedGenre ? ` in ${selectedGenre}` : ""}${page.query ? ` matching "${page.query}"` : ""}.`
    : page.query
      ? `No ${selectedGenre ? `${selectedGenre.toLowerCase()} ` : ""}movies matched "${page.query}" yet.`
      : selectedGenre
        ? `No movies are tagged as ${selectedGenre} yet.`
        : "No movies are in the catalog yet.";
  section.appendChild(createSectionHeading("Movies", "All Movies", subtitle));

  const grid = document.createElement("div");
  grid.className = "poster-grid";
  if (!page.items.length) {
    grid.appendChild(
      createEmptyState(
        page.query
          ? "No movies match this page yet."
          : selectedGenre
            ? `No movies in ${selectedGenre} are available yet.`
            : "No movies are available yet.",
      ),
    );
  } else {
    for (const item of page.items) {
      grid.appendChild(
        createMediaCard(item, {
          compact: true,
          includeYearInTitle: true,
          cardClassName: "movie-page-card",
        }),
      );
    }
  }
  section.appendChild(grid);

  if (page.hasMore || page.loadingMore) {
    const actions = document.createElement("div");
    actions.className = "info-actions";
    const button = createButton(
      page.loadingMore ? "Loading More..." : "Load More Movies",
      page.loadingMore ? "ghost-button" : "primary-button",
      () => {
        loadMoreCatalogMovies().catch((error) => {
          els.pageSubtitle.textContent = `Could not load more movies: ${error.message}`;
        });
      },
    );
    button.disabled = page.loadingMore;
    actions.appendChild(button);
    if (page.hasMore && !page.loadingMore) {
      const hint = document.createElement("p");
      hint.className = "catalog-load-hint";
      hint.textContent = "More posters will load as you scroll, or you can tap here.";
      actions.appendChild(hint);
      observeCatalogAutoLoad(button, () => loadMoreCatalogMovies());
    }
    section.appendChild(actions);
  }

  container.appendChild(section);
}

function renderMovieDetailPage(container, movie) {
  if (!movie) {
    container.appendChild(
      createEmptyState(
        state.catalogMovieLoaded
          ? "That movie is not available in the current media scan."
          : "Loading movie details from the Pi...",
      ),
    );
    return;
  }

  const movieResumeProgress = resumeProgressForItem(movie);
  const intro = document.createElement("section");
  intro.className = "movie-detail-header";

  const topBar = document.createElement("div");
  topBar.className = "movie-detail-topbar";
  topBar.appendChild(
    createButton("Back", "ghost-button movie-detail-back", () => openRoute({ name: "movies" })),
  );

  const posterFrame = document.createElement("div");
  posterFrame.className = "movie-detail-poster-frame";
  posterFrame.style.background = coverGradient(movie.path || movie.title || "movie-detail", "portrait");

  if (movie.posterUrl || movie.backdropUrl) {
    const posterImage = document.createElement("img");
    posterImage.className = "movie-detail-poster-image";
    posterImage.alt = `${movie.title} poster`;
    posterImage.loading = "eager";
    posterImage.decoding = "async";
    posterImage.src = movie.posterUrl || movie.backdropUrl;
    posterFrame.appendChild(posterImage);
  }

  const title = document.createElement("h3");
  title.className = "movie-detail-title";
  title.textContent = titleWithYear(movie.title, movie.year);

  const titleRow = document.createElement("div");
  titleRow.className = "detail-title-row";
  titleRow.appendChild(title);
  const movieRatingChip = createRatingChip(movie.tmdbRating, { className: "rating-chip--hero" });
  if (movieRatingChip) {
    titleRow.appendChild(movieRatingChip);
  }

  const actions = document.createElement("div");
  actions.className = "movie-detail-actions";
  actions.appendChild(
    createButton(movieResumeProgress ? "Resume" : "Play Now", "primary-button", () => playItem(movie)),
  );
  actions.appendChild(createButton("Download", "ghost-button", () => downloadMediaItem(movie)));

  let progressWrap = null;
  if (movieResumeProgress) {
    progressWrap = document.createElement("div");
    progressWrap.className = "card-progress movie-detail-progress";

    const progressLabelNode = document.createElement("p");
    progressLabelNode.className = "card-progress-label movie-detail-progress-label";
    progressLabelNode.textContent = movieResumeProgress.label;
    progressWrap.appendChild(progressLabelNode);

    const progressTrack = document.createElement("div");
    progressTrack.className = "card-progress-track";
    const progressFill = document.createElement("span");
    progressFill.className = "card-progress-fill";
    progressFill.style.width = `${Math.max(0, Math.min(100, Number(movieResumeProgress.percent || 0)))}%`;
    progressTrack.appendChild(progressFill);
    progressWrap.appendChild(progressTrack);
  }

  const summary = document.createElement("p");
  summary.className = "movie-detail-copy";
  summary.textContent =
    movie.overview ||
    movie.tagline ||
    "No movie description is available for this title yet.";

  intro.appendChild(topBar);
  intro.appendChild(posterFrame);
  intro.appendChild(titleRow);
  intro.appendChild(actions);
  if (progressWrap) {
    intro.appendChild(progressWrap);
  }
  intro.appendChild(summary);
  container.appendChild(intro);

  const cards = [
    {
      eyebrow: "Metadata",
      title: "Release And Details",
      rows: [
        { label: "Release date", value: formatDate(movie.releaseDate) || movie.year || "Unknown" },
        { label: "Content rating", value: movie.contentRating || "Unknown" },
        { label: "Genres", value: movie.genres || "Unknown" },
        { label: "Runtime", value: formatRuntime(movie.runtimeMinutes) || "Unknown" },
      ],
      searchText: `${movie.releaseDate || ""} ${movie.year || ""} ${movie.contentRating || ""} ${movie.genres || ""}`,
    },
    {
      eyebrow: "File",
      title: "Format And Size",
      rows: [
        { label: "Format", value: movie.extension || "Unknown" },
        { label: "Size", value: movie.bytes ? formatBytes(movie.bytes) : "Unknown" },
      ],
      searchText: `${movie.extension || ""} ${movie.bytes || ""}`,
      actions: [
        {
          label: "Download",
          className: "ghost-button",
          onClick: () => downloadMediaItem(movie),
        },
      ],
    },
  ];

  const section = document.createElement("section");
  section.className = "content-section movie-detail-section";

  const grid = document.createElement("div");
  grid.className = "device-grid movie-detail-grid";
  const visibleCards = cards.filter((card) =>
    matchesQuery(
      [card.eyebrow, card.title, card.copy, card.searchText]
        .filter(Boolean)
        .join(" "),
    ),
  );

  if (!visibleCards.length) {
    grid.appendChild(createEmptyState("No movie details match this search yet."));
  } else {
    for (const card of visibleCards) {
      grid.appendChild(createInfoCard(card));
    }
  }

  section.appendChild(grid);
  container.appendChild(section);
}

function renderAudiobookDetailPage(container, audiobook) {
  if (!audiobook) {
    container.appendChild(
      createEmptyState(
        state.catalogAudiobookLoaded
          ? "That audiobook is not available in the current media scan."
          : "Loading audiobook details from the Pi...",
      ),
    );
    return;
  }

  const audiobookResumeProgress = resumeProgressForItem(audiobook);
  const intro = document.createElement("section");
  intro.className = "movie-detail-header";

  const topBar = document.createElement("div");
  topBar.className = "movie-detail-topbar";
  topBar.appendChild(
    createButton("Back", "ghost-button movie-detail-back", () => openRoute({ name: "audiobooks" })),
  );

  const posterFrame = document.createElement("div");
  posterFrame.className = "movie-detail-poster-frame";
  posterFrame.style.background = coverGradient(audiobook.path || audiobook.title || "audiobook-detail", "portrait");

  if (audiobook.posterUrl || audiobook.backdropUrl) {
    const posterImage = document.createElement("img");
    posterImage.className = "movie-detail-poster-image audiobook-detail-poster-image";
    posterImage.alt = `${audiobook.title} cover`;
    posterImage.loading = "eager";
    posterImage.decoding = "async";
    posterImage.src = audiobook.posterUrl || audiobook.backdropUrl;
    posterFrame.appendChild(posterImage);
  }

  const title = document.createElement("h3");
  title.className = "movie-detail-title";
  title.textContent = titleWithYear(audiobook.title, audiobook.year);

  const titleRow = document.createElement("div");
  titleRow.className = "detail-title-row";
  titleRow.appendChild(title);

  const actions = document.createElement("div");
  actions.className = "movie-detail-actions";
  actions.appendChild(
    createButton(audiobookResumeProgress ? "Resume" : "Play Now", "primary-button", () => playItem(audiobook)),
  );
  actions.appendChild(createButton("Download", "ghost-button", () => downloadMediaItem(audiobook)));

  let progressWrap = null;
  if (audiobookResumeProgress) {
    progressWrap = document.createElement("div");
    progressWrap.className = "card-progress movie-detail-progress";

    const progressLabelNode = document.createElement("p");
    progressLabelNode.className = "card-progress-label movie-detail-progress-label";
    progressLabelNode.textContent = audiobookResumeProgress.label;
    progressWrap.appendChild(progressLabelNode);

    const progressTrack = document.createElement("div");
    progressTrack.className = "card-progress-track";
    const progressFill = document.createElement("span");
    progressFill.className = "card-progress-fill";
    progressFill.style.width = `${Math.max(0, Math.min(100, Number(audiobookResumeProgress.percent || 0)))}%`;
    progressTrack.appendChild(progressFill);
    progressWrap.appendChild(progressTrack);
  }

  const summary = document.createElement("p");
  summary.className = "movie-detail-copy";
  summary.textContent =
    audiobook.overview ||
    itemSummary(audiobook) ||
    "No audiobook description is available for this title yet.";

  intro.appendChild(topBar);
  intro.appendChild(posterFrame);
  intro.appendChild(titleRow);
  intro.appendChild(actions);
  if (progressWrap) {
    intro.appendChild(progressWrap);
  }
  intro.appendChild(summary);
  container.appendChild(intro);

  const detailsRows = [
    { label: "Author", value: audiobook.artist || "Unknown" },
    { label: "Narrators", value: audiobook.narrators || "Unknown" },
    { label: "Series", value: audiobookSeriesLabel(audiobook) || "Standalone" },
    { label: "Published", value: formatDate(audiobook.releaseDate) || audiobook.year || "Unknown" },
    { label: "Publisher", value: audiobook.publisher || "Unknown" },
    { label: "Language", value: audiobook.language || "Unknown" },
    { label: "Genres", value: audiobook.genres || "Unknown" },
    { label: "Tags", value: audiobook.tags || "Unknown" },
    { label: "Duration", value: formatRuntime(audiobook.runtimeMinutes) || "Unknown" },
  ];
  const collectionLabel = normalizeAudiobookCollectionLabel(audiobook.album);
  const seriesName = audiobookSeriesName(audiobook);
  if (
    collectionLabel &&
    collectionLabel !== audiobook.title &&
    collectionLabel !== seriesName
  ) {
    detailsRows.splice(1, 0, { label: "Collection", value: collectionLabel });
  }

  const cards = [
    {
      eyebrow: "Metadata",
      title: "Book Details",
      rows: detailsRows,
      searchText: [
        audiobook.artist,
        audiobook.narrators,
        audiobook.publisher,
        audiobook.language,
        audiobook.genres,
        audiobook.tags,
        audiobook.seriesName,
        audiobook.seriesIndex,
      ]
        .filter(Boolean)
        .join(" "),
    },
    {
      eyebrow: "File",
      title: "Format And Size",
      rows: [
        { label: "Format", value: audiobook.extension || "Unknown" },
        { label: "Size", value: audiobook.bytes ? formatBytes(audiobook.bytes) : "Unknown" },
      ],
      searchText: `${audiobook.extension || ""} ${audiobook.bytes || ""}`,
      actions: [
        {
          label: "Download",
          className: "ghost-button",
          onClick: () => downloadMediaItem(audiobook),
        },
      ],
    },
  ];

  const section = document.createElement("section");
  section.className = "content-section movie-detail-section";

  const grid = document.createElement("div");
  grid.className = "device-grid movie-detail-grid";
  for (const card of cards) {
    grid.appendChild(createInfoCard(card));
  }

  section.appendChild(grid);
  container.appendChild(section);
}

function renderTvPage(container) {
  const page = state.catalogShows;
  if (!page.loaded) {
    container.appendChild(createEmptyState("Loading a page of shows from the Pi..."));
    return;
  }

  const section = document.createElement("section");
  section.className = "content-section";
  const selectedGenre = genreFilterLabel("tv");
  const subtitle = page.total
    ? `Showing ${page.items.length} of ${page.total} show${page.total === 1 ? "" : "s"}${selectedGenre ? ` in ${selectedGenre}` : ""}${page.query ? ` matching "${page.query}"` : ""}.`
    : page.query
      ? `No ${selectedGenre ? `${selectedGenre.toLowerCase()} ` : ""}shows matched "${page.query}" yet.`
      : selectedGenre
        ? `No shows are tagged as ${selectedGenre} yet.`
        : "No shows are in the catalog yet.";
  section.appendChild(createSectionHeading("TV Shows", "All Shows", subtitle));

  const grid = document.createElement("div");
  grid.className = "poster-grid";
  if (!page.items.length) {
    grid.appendChild(
      createEmptyState(
        page.query
          ? "No shows match this page yet."
          : selectedGenre
            ? `No shows in ${selectedGenre} are available yet.`
            : "No shows are available yet.",
      ),
    );
  } else {
    for (const show of page.items) {
      grid.appendChild(
        createShowCard(show, {
          compact: true,
          includeYearInTitle: true,
          posterLayout: true,
          cardClassName: "movie-page-card",
        }),
      );
    }
  }
  section.appendChild(grid);

  if (page.hasMore || page.loadingMore) {
    const actions = document.createElement("div");
    actions.className = "info-actions";
    const button = createButton(
      page.loadingMore ? "Loading More..." : "Load More Shows",
      page.loadingMore ? "ghost-button" : "primary-button",
      () => {
        loadMoreCatalogShows().catch((error) => {
          els.pageSubtitle.textContent = `Could not load more shows: ${error.message}`;
        });
      },
    );
    button.disabled = page.loadingMore;
    actions.appendChild(button);
    if (page.hasMore && !page.loadingMore) {
      const hint = document.createElement("p");
      hint.className = "catalog-load-hint";
      hint.textContent = "More shows will load as you scroll, or you can tap here.";
      actions.appendChild(hint);
      observeCatalogAutoLoad(button, () => loadMoreCatalogShows());
    }
    section.appendChild(actions);
  }

  container.appendChild(section);
}

function renderShowPage(container, show) {
  if (!show) {
    container.appendChild(
      createEmptyState(
        state.catalogShowLoaded
          ? "That show is not available in the current library scan."
          : "Loading show details from the Pi...",
      ),
    );
    return;
  }

  const intro = document.createElement("section");
  intro.className = "show-detail-header";

  const topBar = document.createElement("div");
  topBar.className = "show-detail-topbar";
  topBar.appendChild(
    createButton("Back", "ghost-button show-detail-back", () => openRoute({ name: "tv" })),
  );

  const title = document.createElement("h3");
  title.className = "show-detail-title";
  title.textContent = titleWithYear(show.title, show.year);

  const titleRow = document.createElement("div");
  titleRow.className = "detail-title-row";
  titleRow.appendChild(title);
  const showRatingChip = createRatingChip(show.tmdbRating, { className: "rating-chip--hero" });
  if (showRatingChip) {
    titleRow.appendChild(showRatingChip);
  }

  const summary = document.createElement("p");
  summary.className = "show-detail-copy";
  summary.textContent =
    show.overview ||
    "No show description is available for this title yet.";

  intro.appendChild(topBar);
  intro.appendChild(titleRow);
  intro.appendChild(summary);
  container.appendChild(intro);

  const seasons = (show.seasons || []).filter((entry) =>
    matchesQuery(
      [
        show.title,
        entry.label,
        seasonCodeLabel(entry),
        ...(entry.episodes || []).map((episode) => searchableMediaText(episode)),
      ]
        .filter(Boolean)
        .join(" "),
    ),
  );

  appendGridSection(container, {
    eyebrow: "",
    title: "Seasons",
    subtitle: "",
    items: seasons,
    renderItem: (entry) => createSeasonCard(show, entry),
    emptyMessage: "No seasons in this show match your search yet.",
  });
}

function renderSeasonPage(container, show, season) {
  if (!show || !season) {
    container.appendChild(
      createEmptyState(
        state.catalogShowLoaded
          ? "That season is not available in the current library scan."
          : "Loading season details from the Pi...",
      ),
    );
    return;
  }

  const intro = document.createElement("section");
  intro.className = "season-detail-header";

  const title = document.createElement("h3");
  title.className = "season-detail-title";
  title.textContent = `${show.title} | ${season.label}`;

  intro.appendChild(title);
  container.appendChild(intro);

  const items = filterMediaItems(season.episodes || []);
  const section = document.createElement("section");
  section.className = "content-section season-detail-section";

  const scroller = document.createElement("div");
  scroller.className = "episode-carousel";

  if (!items.length) {
    scroller.appendChild(createEmptyState("No episodes in this season match your search yet."));
  } else {
    for (const item of items) {
      const card = createEpisodeCard(item);
      card.classList.add("episode-carousel-card");
      scroller.appendChild(card);
    }
  }

  section.appendChild(scroller);
  container.appendChild(section);
}

function renderMusicPage(container) {
  const items = filterMediaItems(librarySections().music);
  appendGridSection(container, {
    eyebrow: "Music",
    title: "All Tracks",
    subtitle: `${items.length} track${items.length === 1 ? "" : "s"} in this page view.`,
    items,
    renderItem: createMediaCard,
    emptyMessage: "No tracks match this page yet.",
  });
}

function renderAudiobookPage(container) {
  const page = state.catalogAudiobooks;
  const browseTarget = currentAudiobookBrowseTarget();
  const isFilteredBrowse = Boolean(browseTarget.kind);
  if (!page.loaded) {
    container.appendChild(createEmptyState("Loading a page of audiobooks from the Pi..."));
    return;
  }

  const baseItems = Array.isArray(page.items) ? page.items : [];
  const items = browseTarget.kind === "collection" ? [...baseItems].sort(compareAudiobookSeriesOrder) : baseItems;
  const collectionGroups = buildAudiobookBrowseGroups(items, "collection");
  const authorGroups = buildAudiobookBrowseGroups(items, "author");
  const selectedGenre = genreFilterLabel("audiobooks");
  const hasCollectionGroups = collectionGroups.length > 0;
  const hasAuthorGroups = authorGroups.length > 0;
  const hasBrowseGroups = hasCollectionGroups || hasAuthorGroups;

  if (!isFilteredBrowse && hasCollectionGroups) {
    appendCarouselSection(container, {
      eyebrow: "Collections",
      title: "Browse By Collection",
      subtitle: "Swipe through audiobook collections, then tap one to narrow the shelf below.",
      items: collectionGroups,
      renderItem: createAudiobookGroupCard,
      itemClassName: "episode-carousel-card",
      emptyMessage: "No audiobook collections match this page yet.",
    });
  }

  if (!isFilteredBrowse && hasAuthorGroups) {
    appendCarouselSection(container, {
      eyebrow: "Authors",
      title: "Browse By Author",
      subtitle: "Swipe through authors, then tap one to focus on their audiobooks.",
      items: authorGroups,
      renderItem: createAudiobookGroupCard,
      itemClassName: "episode-carousel-card",
      emptyMessage: "No audiobook authors match this page yet.",
    });
  }

  const section = document.createElement("section");
  section.className = "content-section";
  const sectionHeading =
    browseTarget.kind === "collection"
      ? "Books In Collection"
      : browseTarget.kind === "author"
        ? "Books By Author"
        : "All Audiobooks";
  const browseSubject =
    browseTarget.kind === "collection"
      ? `this collection${browseTarget.label ? ` (${browseTarget.label})` : ""}`
      : browseTarget.kind === "author"
        ? `this author${browseTarget.label ? ` (${browseTarget.label})` : ""}`
        : "";
  const subtitle = page.total
    ? [
        `Showing ${page.items.length} of ${page.total} audiobook${page.total === 1 ? "" : "s"}${selectedGenre ? ` in ${selectedGenre}` : ""}${page.query ? ` matching "${page.query}"` : ""}.`,
        !isFilteredBrowse && !hasBrowseGroups && items.length
          ? "Author and collection rows will appear automatically when that info is available from tags or folder names."
          : "",
      ]
        .filter(Boolean)
        .join(" ")
    : page.query
      ? `No ${selectedGenre ? `${selectedGenre.toLowerCase()} ` : ""}audiobooks${browseSubject ? ` in ${browseSubject}` : ""} matched "${page.query}" yet.`
      : selectedGenre
        ? `No audiobooks${browseSubject ? ` in ${browseSubject}` : ""} are tagged as ${selectedGenre} yet.`
        : browseSubject
          ? `No audiobooks are available in ${browseSubject} yet.`
          : "No audiobooks are in the catalog yet.";
  section.appendChild(createSectionHeading("Audiobooks", sectionHeading, subtitle));

  const grid = document.createElement("div");
  grid.className = "poster-grid";
  if (!page.items.length) {
    grid.appendChild(
      createEmptyState(
        page.query
          ? `No audiobooks${browseSubject ? ` in ${browseSubject}` : ""} match this page yet.`
          : selectedGenre
            ? `No audiobooks${browseSubject ? ` in ${browseSubject}` : ""} in ${selectedGenre} are available yet.`
            : browseSubject
              ? `No audiobooks are available in ${browseSubject} yet.`
              : "No audiobooks are available yet.",
      ),
    );
  } else {
    for (const item of page.items) {
      grid.appendChild(createAudiobookShelfCard(item));
    }
  }
  section.appendChild(grid);

  if (page.hasMore || page.loadingMore) {
    const actions = document.createElement("div");
    actions.className = "info-actions";
    const button = createButton(
      page.loadingMore ? "Loading More..." : "Load More Audiobooks",
      page.loadingMore ? "ghost-button" : "primary-button",
      () => {
        loadMoreCatalogAudiobooks().catch((error) => {
          els.pageSubtitle.textContent = `Could not load more audiobooks: ${error.message}`;
        });
      },
    );
    button.disabled = page.loadingMore;
    actions.appendChild(button);
    if (page.hasMore && !page.loadingMore) {
      const hint = document.createElement("p");
      hint.className = "catalog-load-hint";
      hint.textContent = "More audiobooks will load as you scroll, or you can tap here.";
      actions.appendChild(hint);
      observeCatalogAutoLoad(button, () => loadMoreCatalogAudiobooks());
    }
    section.appendChild(actions);
  }

  container.appendChild(section);
}

function renderDocumentPage(container, documentBrowser) {
  const browser = documentBrowser || buildDocumentBrowserState(state.route.folder);

  if (!browser.exists) {
    container.appendChild(createEmptyState("That folder is not in the current media scan."));
    return;
  }

  if (!browser.folders.length && !browser.files.length) {
    container.appendChild(
      createEmptyState(
        state.query
          ? "No folders or files in this location match your search yet."
          : "This folder is empty right now.",
      ),
    );
    return;
  }

  if (browser.folders.length) {
    appendGridSection(container, {
      eyebrow: browser.currentFolder ? "Subfolders" : "Folders",
      title: browser.currentFolder ? "Folders In This Location" : "Document Folders",
      subtitle: browser.currentFolder
        ? "Open a folder to keep drilling into maps, permits, guides, and reference files."
        : "Browse storage by folder first, then open files within each location.",
      items: browser.folders,
      renderItem: createFolderCard,
      gridClass: "poster-grid",
      emptyMessage: "No folders match this view yet.",
    });
  }

  if (browser.files.length) {
    appendGridSection(container, {
      eyebrow: "Files",
      title: browser.currentFolder ? "Files In This Folder" : "Files At The Root",
      subtitle: `${browser.files.length} file${browser.files.length === 1 ? "" : "s"} in this page view.`,
      items: browser.files,
      renderItem: createDocumentCard,
      gridClass: "poster-grid",
      emptyMessage: "No files match this view yet.",
    });
  }
}

function posterDebugDetails() {
  const library = state.library || {};
  const sections = library.sections || {};
  const versionToken =
    (state.status && state.status.metadataGeneratedAt) ||
    (library.metadata && library.metadata.generatedAt) ||
    "";
  const movies = Array.isArray(sections.movies) ? sections.movies : [];
  const shows = Array.isArray(sections.tv) ? sections.tv : [];
  const tvEpisodes = [];
  for (const show of shows) {
    for (const season of show.seasons || []) {
      for (const episode of season.episodes || []) {
        tvEpisodes.push(episode);
      }
    }
  }

  const posterCandidate = (entry) => entry && (entry.posterPath || entry.posterUrl);
  const artCandidate = (entry) => entry && (entry.posterPath || entry.posterUrl || entry.backdropPath || entry.backdropUrl);

  const candidate =
    movies.find(posterCandidate) ||
    shows.find(posterCandidate) ||
    tvEpisodes.find(posterCandidate) ||
    movies.find(artCandidate) ||
    shows.find(artCandidate) ||
    tvEpisodes.find(artCandidate);

  if (!candidate) {
    return null;
  }

  const artPath = candidate.posterPath || candidate.backdropPath || "";
  const artUrl =
    candidate.posterUrl ||
    candidate.backdropUrl ||
    buildAssetUrl(artPath, versionToken);

  if (!artPath && !artUrl) {
    return null;
  }

  const moviesWithPoster = movies.filter(posterCandidate).length;
  const showsWithPoster = shows.filter(posterCandidate).length;

  return {
    title: candidate.title || candidate.showTitle || "Unknown title",
    section:
      candidate.section === "tv"
        ? "TV episode"
        : candidate.section === "movies"
          ? "Movie"
          : candidate.section || "Library item",
    artKind: candidate.posterPath || candidate.posterUrl ? "Poster" : "Backdrop fallback",
    artPath,
    artUrl: absoluteUrl(artUrl),
    metadataSource: candidate.metadataSource || "Unknown",
    moviePosterCount: moviesWithPoster,
    showPosterCount: showsWithPoster,
  };
}

function renderDevicePage(container) {
  const status = state.status;
  const library = state.library;

  if (!status || !library) {
    container.appendChild(createEmptyState("Loading device information from the Raspberry Pi Zero W..."));
    return;
  }

  const summary = counts();
  const metadata = library.metadata || {};
  const preferredUrl = status.mdnsReady ? status.mdnsUrl : status.ipAppUrl || status.appUrl;
  const lastPlayed = joinBits([status.lastPlayed, status.lastPlayedType]);
  const currentNetworkName = activeNetworkName(status);
  const hotspotName = hotspotNetworkName(status);
  const hotspotPassword = status.hotspotPassword || (status.networkMode === "hotspot" ? status.password : "");
  const fileManager = status.fileManager || {};
  const fileManagerPassword = String(fileManager.password || "").trim();
  let fileManagerUrl = String(fileManager.url || "").trim();
  if (Number(fileManager.port || 0) > 0) {
    try {
      const currentUrl = new URL(window.location.href);
      currentUrl.port = String(fileManager.port);
      currentUrl.pathname = "/";
      currentUrl.search = "";
      currentUrl.hash = "";
      fileManagerUrl = currentUrl.toString();
    } catch (error) {
      // Fall back to the server-provided URL if the current location cannot be rewritten.
    }
  }
  const posterDebug = posterDebugDetails();
  const indexUrl = libraryIndexUrl();
  const librarySourceLabel =
    state.librarySource === "sd-index"
      ? "Storage index"
      : state.librarySource === "server-api"
        ? "Server API fallback"
        : "Unknown";
  const cards = [
    {
      eyebrow: "This Device",
      title: "Reset Browser Data",
      copy: "Use these only if this browser gets out of sync or you want to clear this device profile's watch history.",
      actions: [
        {
          label: "Clear Cache Keep History",
          className: "ghost-button",
          onClick: () => {
            clearClientAppData({ keepWatchHistory: true }).catch((error) => {
              els.pageSubtitle.textContent = `Cache reset failed: ${error.message}`;
            });
          },
        },
        {
          label: "Wipe This Device",
          className: "ghost-button",
          onClick: () => {
            clearClientAppData({
              keepWatchHistory: false,
              confirmMessage:
                `Wipe the local ${appDisplayName()} app data and this device profile's watch history on the Pi? This will not delete any media from the library.`,
            }).catch((error) => {
              els.pageSubtitle.textContent = `Device wipe failed: ${error.message}`;
            });
          },
        },
      ],
      searchText: "this device browser data reset cache clear watch history wipe local app data profile",
    },
    {
      eyebrow: "Metadata",
      title: "Metadata Refresh Output",
      copy: "Watch the TMDb metadata command line by line during online rescans.",
      renderCard: () => createMetadataRefreshCard(),
      searchText:
        "metadata refresh output tmdb progress rescan command log online status live output recent lines script",
    },
    {
      eyebrow: "Config",
      title: "Device Settings",
      copy: "Change the server name and fallback Wi-Fi details without opening the Pi shell.",
      renderCard: () => createDeviceConfigCard(),
      searchText: `config settings server name device name hotspot wifi ssid password ${status.device || ""} ${hotspotName || ""} ${hotspotPassword || ""}`,
    },
    {
      eyebrow: "Upload",
      title: "Add Media Over Wi-Fi",
      copy: "Send files or whole folders to any existing media path, or create a new folder as you upload.",
      renderCard: () => createUploadCard(),
      searchText: "upload media files folders wifi device panel add media over wifi browse choose files choose folder season show destination path rescan",
    },
    {
      eyebrow: "Files",
      title: "File Management",
      copy:
        "Open the bundled File Browser to rename, move, or remove media directly on the Pi. The password shown here is the initial admin password captured from File Browser's first-start logs.",
      rows: [
        { label: "User", value: fileManager.username || "admin" },
        {
          label: "Initial admin password",
          value: fileManagerPassword || "Unavailable. Rerun update.sh on the Pi to recapture it.",
        },
        { label: "URL", value: fileManagerUrl || "Unavailable" },
        { label: "Root", value: fileManager.root || status.mediaRoot || "/media" },
      ],
      actions: [
        {
          label: "Open File Browser",
          className: "primary-button",
          onClick: () => {
            if (!fileManagerUrl) {
              els.pageSubtitle.textContent = "File Browser URL is unavailable right now.";
              return;
            }
            window.open(fileManagerUrl, "_blank", "noopener,noreferrer");
          },
        },
        ...(fileManagerPassword
          ? [
              {
                label: "Copy Password",
                className: "ghost-button",
                onClick: () => {
                  copyTextToClipboard(fileManagerPassword)
                    .then((copied) => {
                      els.pageSubtitle.textContent = copied
                        ? "File Browser password copied to the clipboard."
                        : fileManagerPassword;
                    })
                    .catch(() => {
                      els.pageSubtitle.textContent = fileManagerPassword;
                    });
                },
              },
            ]
          : []),
      ],
      searchText: `file browser file management admin login ${fileManagerUrl || ""} ${fileManager.root || status.mediaRoot || ""}`,
    },
    {
      eyebrow: "Network",
      title: "Hotspot / Network",
      rows: [
        { label: "Device", value: status.device || appDisplayName() },
        { label: "Connection mode", value: networkModeLabel(status) },
        { label: "Current network", value: currentNetworkName || "Unavailable" },
        { label: "Fallback hotspot", value: hotspotName || "Unavailable" },
        { label: "Hotspot password", value: hotspotPassword || "Unavailable" },
        { label: "Preferred URL", value: preferredUrl || "Unavailable" },
        { label: "Fallback URL", value: status.ipAppUrl || "Unavailable" },
        { label: "mDNS", value: status.mdnsReady ? status.mdnsHost || "Ready" : "Unavailable" },
      ],
      searchText: `${status.device || ""} ${status.networkMode || ""} ${currentNetworkName || ""} ${hotspotName || ""} ${hotspotPassword || ""} ${preferredUrl || ""} ${status.ipAppUrl || ""} ${status.mdnsHost || ""}`,
    },
    {
      eyebrow: "Status",
      title: "Device Health",
      rows: [
        { label: "Media storage", value: status.sdMounted ? "Mounted and ready" : "Not available" },
        { label: "Connected clients", value: String(status.clients || 0) },
        { label: "Media root", value: status.mediaRoot || "/media" },
        {
          label: "Upload staging",
          value: status.uploadTempRoot
            ? `${status.uploadTempRoot}${status.uploadTempReady === false ? " (check permissions)" : ""}`
            : "Unavailable",
        },
        { label: "Last playback", value: lastPlayed || "Nothing played yet" },
      ],
      searchText: `${status.sdMounted ? "mounted ready" : "not available"} ${status.clients || 0} ${status.mediaRoot || ""} ${status.uploadTempRoot || ""} ${lastPlayed || ""}`,
    },
    {
      eyebrow: "Library",
      title: "Content Snapshot",
      rows: [
        { label: "Movies", value: String(summary.movies) },
        { label: "Shows", value: String(summary.shows) },
        { label: "Episodes", value: String(summary.episodes) },
        { label: "Tracks", value: String(summary.music) },
        { label: "Audiobooks", value: String(summary.audiobooks) },
        { label: "Documents", value: String(summary.documents) },
      ],
      searchText: `movies ${summary.movies} shows ${summary.shows} episodes ${summary.episodes} tracks ${summary.music} audiobooks ${summary.audiobooks} documents ${summary.documents}`,
    },
    {
      eyebrow: "Metadata",
      title: "Index Details",
      rows: [
        { label: "Available", value: metadata.available ? "Yes" : "No" },
        { label: "Loaded from", value: librarySourceLabel },
        { label: "Item records", value: String(metadata.itemCount || 0) },
        { label: "Show records", value: String(metadata.showCount || 0) },
        { label: "Generator", value: metadata.generator || "Unknown" },
        { label: "Updated", value: formatTimestamp(metadata.generatedAt) || "Unknown" },
        { label: "Index URL", value: indexUrl },
      ],
      actions: [
        {
          label: "Open Index JSON",
          className: "ghost-button",
          onClick: () => {
            window.open(indexUrl, "_blank", "noopener,noreferrer");
          },
        },
      ],
      searchText: `${metadata.available ? "yes" : "no"} ${metadata.itemCount || 0} ${metadata.showCount || 0} ${metadata.generator || ""} ${metadata.generatedAt || ""}`,
    },
    posterDebug
      ? {
          eyebrow: "Poster Debug",
          title: "Artwork URL Test",
          copy: "Open this direct asset URL on the client. If it loads there but not in the grid, the issue is in rendering rather than SD access.",
          rows: [
            { label: "Sample title", value: posterDebug.title },
            { label: "Type", value: posterDebug.section },
            { label: "Artwork", value: posterDebug.artKind },
            { label: "Loaded from", value: librarySourceLabel },
            { label: "Metadata source", value: posterDebug.metadataSource },
            { label: "Movie posters", value: String(posterDebug.moviePosterCount) },
            { label: "Show posters", value: String(posterDebug.showPosterCount) },
            { label: "Asset path", value: posterDebug.artPath },
            { label: "Asset URL", value: posterDebug.artUrl },
          ],
          actions: [
            {
              label: "Open Asset URL",
              className: "primary-button",
              onClick: () => {
                window.open(posterDebug.artUrl, "_blank", "noopener,noreferrer");
              },
            },
            {
              label: "Copy Asset URL",
              className: "ghost-button",
              onClick: () => {
                copyTextToClipboard(posterDebug.artUrl)
                  .then((copied) => {
                    els.pageSubtitle.textContent = copied
                      ? "Poster asset URL copied to the clipboard."
                      : posterDebug.artUrl;
                  })
                  .catch(() => {
                    els.pageSubtitle.textContent = posterDebug.artUrl;
                  });
              },
            },
            {
              label: "Open Index JSON",
              className: "ghost-button",
              onClick: () => {
                window.open(indexUrl, "_blank", "noopener,noreferrer");
              },
            },
          ],
          searchText: `${posterDebug.title} ${posterDebug.section} ${posterDebug.artKind} ${posterDebug.artPath} ${posterDebug.artUrl} ${posterDebug.metadataSource}`,
        }
      : {
          eyebrow: "Poster Debug",
          title: "Artwork URL Test",
          copy: "No poster or backdrop paths were found in the current client-side library data yet. That points to the metadata/index side rather than browser image decoding.",
          rows: [
            { label: "Loaded from", value: librarySourceLabel },
            { label: "Movie posters", value: "0" },
            { label: "Show posters", value: "0" },
            { label: "Index URL", value: indexUrl },
          ],
          actions: [
            {
              label: "Open Index JSON",
              className: "primary-button",
              onClick: () => {
                window.open(indexUrl, "_blank", "noopener,noreferrer");
              },
            },
          ],
          searchText: "poster debug artwork url test no poster backdrop paths",
        },
  ];

  const section = document.createElement("section");
  section.className = "content-section";
  section.appendChild(
    createSectionHeading(
      "Device",
      "Admin Console",
      "Keep the portable server healthy, confirm how the current media storage was indexed, and run maintenance actions without leaving the app.",
    ),
  );

  const grid = document.createElement("div");
  grid.className = "device-grid";
  const visibleCards = cards.filter((card) =>
    matchesQuery(
      [card.eyebrow, card.title, card.copy, card.searchText]
        .filter(Boolean)
        .join(" "),
    ),
  );

  if (!visibleCards.length) {
    grid.appendChild(createEmptyState("No device details match this search yet."));
  } else {
    for (const card of visibleCards) {
      grid.appendChild(card.renderCard ? card.renderCard() : createInfoCard(card));
    }
  }

  section.appendChild(grid);
  container.appendChild(section);
}

function stopDeviceStatusPolling() {
  if (deviceStatusPollTimer) {
    window.clearTimeout(deviceStatusPollTimer);
    deviceStatusPollTimer = 0;
  }
}

function shouldPollDeviceStatus() {
  return state.route.name === "device" && !document.hidden && !state.uploadingLocally;
}

function scheduleDeviceStatusPolling(delayMs) {
  stopDeviceStatusPolling();
  if (!shouldPollDeviceStatus()) {
    return;
  }
  deviceStatusPollTimer = window.setTimeout(pollDeviceStatus, delayMs);
}

async function pollDeviceStatus() {
  if (!shouldPollDeviceStatus()) {
    stopDeviceStatusPolling();
    return;
  }
  if (deviceStatusPollInFlight) {
    scheduleDeviceStatusPolling(1200);
    return;
  }

  deviceStatusPollTimer = 0;
  deviceStatusPollInFlight = true;
  const previousUpload = uploadStatusSnapshot();
  const previousMetadata = metadataRefreshSnapshot();

  try {
    await loadStatus();
    const nextUpload = uploadStatusSnapshot();
    const nextMetadata = metadataRefreshSnapshot();
    refreshLiveUploadActivity(nextUpload);
    refreshLiveMetadataActivity(nextMetadata);
    const completionKey = uploadCompletionRefreshKey(nextUpload);
    if (completionKey && completionKey !== lastCompletedUploadRefreshKey) {
      state.preferServerLibrary = true;
      await loadLibrary();
      lastCompletedUploadRefreshKey = completionKey;
    }

    if (uploadCompletionRefreshKey(previousUpload) && !completionKey) {
      lastCompletedUploadRefreshKey = "";
    }

    if (metadataRefreshIsActive(previousMetadata) && !metadataRefreshIsActive(nextMetadata)) {
      state.preferServerLibrary = true;
      await loadLibrary();
    }
  } catch (error) {
    console.warn("Device status poll failed", error);
  } finally {
    deviceStatusPollInFlight = false;
    scheduleDeviceStatusPolling(
      uploadIsActive(uploadStatusSnapshot()) || metadataRefreshIsActive(metadataRefreshSnapshot()) ? 900 : 2500,
    );
  }
}

function syncDeviceStatusPolling() {
  if (!shouldPollDeviceStatus()) {
    stopDeviceStatusPolling();
    return;
  }
  if (!deviceStatusPollTimer) {
    scheduleDeviceStatusPolling(
      uploadIsActive(uploadStatusSnapshot()) || metadataRefreshIsActive(metadataRefreshSnapshot()) ? 900 : 2500,
    );
  }
}

function ensureUploadDestinationsLoaded() {
  if (state.route.name !== "device" || state.uploadDestinations.length || uploadDestinationsRequest) {
    return;
  }

  uploadDestinationsRequest = loadUploadDestinations()
    .then(() => {
      if (state.route.name === "device" && !state.uploadSelectionLocked && !state.uploadingLocally) {
        render();
      }
    })
    .catch((error) => {
      console.warn("Unable to load upload destinations", error);
    })
    .finally(() => {
      uploadDestinationsRequest = null;
    });
}

function ensureDeviceConfigLoaded() {
  if (state.route.name !== "device" || state.deviceConfigLoaded || deviceConfigRequest) {
    return;
  }

  deviceConfigRequest = loadDeviceConfig()
    .then((config) => {
      state.deviceConfigDraft = {
        deviceName: String(config.deviceName || state.deviceConfigDraft.deviceName || "").trim(),
        hotspotSsid: String(config.hotspotSsid || state.deviceConfigDraft.hotspotSsid || "").trim(),
        wifiPassword: String(config.wifiPassword || state.deviceConfigDraft.wifiPassword || "").trim(),
        tmdbApiKey: String(config.tmdbApiKey || state.deviceConfigDraft.tmdbApiKey || "").trim(),
      };
      state.deviceConfigLoaded = true;
      render();
    })
    .catch((error) => {
      console.warn("Unable to load device config", error);
    })
    .finally(() => {
      deviceConfigRequest = null;
    });
}

function renderNav() {
  for (const link of els.nav) {
    link.classList.remove("is-active");
    const route = parseRoute(new URL(link.href, window.location.origin).pathname);
    const isActive =
      route.name === state.route.name ||
      (route.name === "tv" && state.route.name === "show") ||
      (route.name === "tv" && state.route.name === "season") ||
      (route.name === "movies" && state.route.name === "movie") ||
      (route.name === "audiobooks" &&
        ["audiobook", "audiobookCollection", "audiobookAuthor"].includes(state.route.name));
    if (isActive) {
      link.classList.add("is-active");
    }
  }
}

function render() {
  updateBranding();
  const show =
    state.route.name === "show" || state.route.name === "season"
      ? findShow(state.route.slug)
      : null;
  const movie = state.route.name === "movie" ? findMediaItemByPath(state.route.path) : null;
  const audiobook = state.route.name === "audiobook" ? findMediaItemByPath(state.route.path) : null;
  const season = state.route.name === "season" ? findSeason(show, state.route.seasonKey) : null;
  const documentBrowser =
    state.route.name === "documents" ? buildDocumentBrowserState(state.route.folder) : null;

  renderNav();
  renderBreadcrumbs(show, movie, season, audiobook, documentBrowser);
  updatePageHeader(show, movie, season, audiobook, documentBrowser);
  renderPageFilters();
  updatePageActions(show, movie, season, documentBrowser);
  renderHero(show, movie, season, documentBrowser);

  disconnectCatalogAutoLoadObserver();
  els.content.innerHTML = "";

  const requiresFullLibrary = ["music", "documents", "device"].includes(state.route.name);
  if (requiresFullLibrary && !state.library) {
    els.content.appendChild(createEmptyState("Loading library from the storage index..."));
    return;
  }

  if (state.route.name === "movies") {
    renderMoviePage(els.content);
  } else if (state.route.name === "movie") {
    renderMovieDetailPage(els.content, movie);
  } else if (state.route.name === "tv") {
    renderTvPage(els.content);
  } else if (state.route.name === "show") {
    renderShowPage(els.content, show);
  } else if (state.route.name === "season") {
    renderSeasonPage(els.content, show, season);
  } else if (state.route.name === "music") {
    renderMusicPage(els.content);
  } else if (isAudiobookBrowseRoute()) {
    renderAudiobookPage(els.content);
  } else if (state.route.name === "audiobook") {
    renderAudiobookDetailPage(els.content, audiobook);
  } else if (state.route.name === "documents") {
    renderDocumentPage(els.content, documentBrowser);
  } else if (state.route.name === "device") {
    renderDevicePage(els.content);
  } else {
    renderHomePage(els.content);
  }

  syncDeviceStatusPolling();
  ensureUploadDestinationsLoaded();
  ensureDeviceConfigLoaded();
}

async function loadStatus() {
  const response = await fetch("/api/status");
  state.status = await response.json();
  state.preferServerLibrary = Boolean((state.status && state.status.preferServerLibrary) || state.preferServerLibrary);
}

async function loadUploadDestinations() {
  const response = await fetch("/api/upload-destinations", { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Upload destinations returned HTTP ${response.status}`);
  }

  const payload = await response.json();
  state.uploadDestinations = Array.isArray(payload.paths) ? payload.paths : [];
  if (!normalizeUploadDestinationPath(state.uploadDraft.destination)) {
    state.uploadDraft.destination = defaultUploadDestination();
  }
}

async function loadLibraryFromIndex() {
  const versionToken = state.status && state.status.metadataGeneratedAt ? state.status.metadataGeneratedAt : "";
  const response = await fetch(buildAssetUrl("/media/.nomadscreen/library.json", versionToken), {
    cache: versionToken ? "force-cache" : "default",
  });

  if (!response.ok) {
    throw new Error(`Static library index returned HTTP ${response.status}`);
  }

  return buildLibraryFromIndex(await response.json());
}

async function loadLibrary() {
  if (!state.preferServerLibrary) {
    try {
      state.library = await loadLibraryFromIndex();
      state.librarySource = "sd-index";
    } catch (error) {
      console.warn("Falling back to server library API", error);
      const response = await fetch("/api/library", { cache: "no-store" });
      state.library = await response.json();
      state.librarySource = "server-api";
    }
  } else {
    const response = await fetch("/api/library", { cache: "no-store" });
    state.library = await response.json();
    state.librarySource = "server-api";
  }

  if (state.playingItem) {
    const replacement = allMediaItems().find((item) => item.path === state.playingItem.path) || null;
    state.playingItem = replacement;
    if (replacement) {
      renderPlayerDetails(replacement);
    } else {
      resetPlayers();
      renderPlayerDetails(null);
    }
  }

  if (state.route.name === "show" && !findShow(state.route.slug)) {
    openRoute({ name: "tv" }, true);
    return;
  }

  if (state.route.name === "season") {
    const show = findShow(state.route.slug);
    if (!show) {
      openRoute({ name: "tv" }, true);
      return;
    }
    if (!findSeason(show, state.route.seasonKey)) {
      openRoute({ name: "show", slug: show.slug }, true);
      return;
    }
  }

  if (state.route.name === "movie" && !findMediaItemByPath(state.route.path)) {
    openRoute({ name: "movies" }, true);
    return;
  }

  render();
}

async function refreshAll() {
  await loadStatus();
  if (!state.playbackLoaded) {
    try {
      await (playbackStateRequest || (playbackStateRequest = loadServerPlaybackState()));
    } catch (error) {
      console.warn("Could not load server playback state", error);
      state.playbackProgress = {};
      state.watchedOverrides = {};
      state.playbackProfile = null;
      state.playbackLoaded = true;
    } finally {
      playbackStateRequest = null;
    }
  }
  await loadRouteData();
  if (routeGenreSection()) {
    try {
      await (catalogGenresRequest || (catalogGenresRequest = loadCatalogGenres()));
    } finally {
      catalogGenresRequest = null;
    }
  }
  if (state.route.name === "device") {
    await (uploadDestinationsRequest || loadUploadDestinations());
    const config = await (deviceConfigRequest || loadDeviceConfig());
    state.deviceConfigDraft = {
      deviceName: String(config.deviceName || state.deviceConfigDraft.deviceName || "").trim(),
      hotspotSsid: String(config.hotspotSsid || state.deviceConfigDraft.hotspotSsid || "").trim(),
      wifiPassword: String(config.wifiPassword || state.deviceConfigDraft.wifiPassword || "").trim(),
      tmdbApiKey: String(config.tmdbApiKey || state.deviceConfigDraft.tmdbApiKey || "").trim(),
    };
    state.deviceConfigLoaded = true;
  }
  lastCompletedUploadRefreshKey = uploadCompletionRefreshKey(uploadStatusSnapshot());
  render();
}

async function refreshAllWithRetry(options = {}) {
  const attempts = Math.max(1, Number(options.attempts) || 1);
  const delayMs = Math.max(0, Number(options.delayMs) || 0);
  let lastError = null;

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      await refreshAll();
      return;
    } catch (error) {
      lastError = error;
      if (attempt < attempts && delayMs > 0) {
        await delay(delayMs * attempt);
      }
    }
  }

  throw lastError || new Error("Refresh failed.");
}

document.addEventListener("click", (event) => {
  const link = event.target.closest("[data-route]");
  if (!link) {
    return;
  }

  event.preventDefault();
  openRoute(link.getAttribute("href"));
});

window.addEventListener("popstate", () => {
  if (routeQueryRefreshTimer) {
    window.clearTimeout(routeQueryRefreshTimer);
    routeQueryRefreshTimer = 0;
  }
  clearSearch();
  state.route = parseRoute(window.location.pathname);
  prepareRouteDataForLoading();
  render();
  refreshAll().catch((error) => {
    els.pageSubtitle.textContent = `Unable to reach the server: ${error.message}`;
  });
});

window.addEventListener("beforeunload", () => persistActivePlaybackProgress(true, false, false, true));
window.addEventListener("pagehide", () => persistActivePlaybackProgress(true, false, false, true));
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    persistActivePlaybackProgress(true, false, false, true);
    stopDeviceStatusPolling();
  } else {
    syncDeviceStatusPolling();
  }
});

els.search.addEventListener("input", (event) => {
  state.query = event.target.value.trim();
  if (routeUsesServerSearch()) {
    prepareRouteDataForLoading();
    render();
    scheduleRouteQueryRefresh();
    return;
  }
  render();
});

state.playbackProgress = {};
state.watchedOverrides = {};
resetPlayers();
renderPlayerDetails(null);
attachPlayerDiagnostics();
render();
prepareRouteDataForLoading();
refreshAll().catch((error) => {
  els.pageSubtitle.textContent = `Unable to reach the server: ${error.message}`;
  els.content.innerHTML = "";
  els.content.appendChild(createEmptyState("The server API is not responding yet."));
});
