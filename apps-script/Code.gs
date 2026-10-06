/**
 * POI Scraper - Apps Script API + Dashboard
 * Menghubungkan Google Sheet (DB) + GitHub Actions (worker).
 *
 * Deploy sebagai Web App (execute as: me, access: anyone).
 */

var JOB_HEADERS = [
  "job_id", "user", "country", "brand", "spec_json", "status", "progress",
  "created_at", "started_at", "finished_at", "output_file_id", "output_url", "error",
  "result_sheet", "result_gid", "current_target", "listings_found", "records", "eta", "run_url"
];
var USER_HEADERS = [
  "email", "api_key_hash", "quota", "used", "active", "created_at"
];
var LOG_HEADERS = ["timestamp", "job_id", "user", "message"];
var APP_VERSION = "2026-10-06.4";

// ------------------------------------------------------------
// Properties & Sheet helpers
// ------------------------------------------------------------
function props_() {
  return PropertiesService.getScriptProperties().getProperties();
}

function prop_(key, fallback) {
  var v = PropertiesService.getScriptProperties().getProperty(key);
  return v === null || v === "" ? (fallback || "") : v;
}

function sheet_() {
  var id = prop_("SHEET_ID");
  if (!id) throw new Error("SHEET_ID belum diset di Script Properties.");
  return SpreadsheetApp.openById(id);
}

function tab_(name, headers) {
  var ss = sheet_();
  var sh = ss.getSheetByName(name);
  if (!sh) {
    sh = ss.insertSheet(name);
    sh.appendRow(headers);
  }
  return sh;
}

function readTable_(name, headers) {
  var sh = tab_(name, headers);
  var values = sh.getDataRange().getValues();
  if (!values.length) return [];
  var head = values.shift().map(function (h) { return String(h).trim(); });
  return values.filter(function (r) { return String(r[0]).length > 0; }).map(function (r) {
    var o = {};
    head.forEach(function (h, i) { o[h] = r[i]; });
    return o;
  });
}

function nowStr_() {
  return Utilities.formatDate(new Date(), "Asia/Jakarta", "yyyy-MM-dd HH:mm:ss");
}

function fmtVal_(v) {
  if (v instanceof Date) return Utilities.formatDate(v, "Asia/Jakarta", "yyyy-MM-dd HH:mm:ss");
  return v === null || v === undefined ? "" : String(v);
}

function hashKey_(key) {
  var bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, String(key));
  return bytes.map(function (b) { return ("0" + (b & 0xff).toString(16)).slice(-2); }).join("");
}

// ------------------------------------------------------------
// Users & auth
// ------------------------------------------------------------
function findUserByEmail_(email) {
  email = String(email || "").toLowerCase();
  var users = readTable_("Users", USER_HEADERS);
  for (var i = 0; i < users.length; i++) {
    if (String(users[i].email).toLowerCase() === email) return users[i];
  }
  return null;
}

function findUserByKey_(key) {
  var h = hashKey_(key);
  var users = readTable_("Users", USER_HEADERS);
  for (var i = 0; i < users.length; i++) {
    if (String(users[i].api_key_hash) === h) return users[i];
  }
  return null;
}

function appendUser_(email, keyHash, quota, active) {
  var sh = tab_("Users", USER_HEADERS);
  sh.appendRow([email, keyHash, quota, 0, active, nowStr_()]);
  return { email: email, api_key_hash: keyHash, quota: quota, used: 0, active: active };
}

function verifyGoogleToken_(idToken) {
  var cache = CacheService.getScriptCache();
  var cacheKey = "tok_" + hashKey_(idToken).slice(0, 40);
  var cached = cache.get(cacheKey);
  if (cached) return cached;

  var res = UrlFetchApp.fetch(
    "https://oauth2.googleapis.com/tokeninfo?id_token=" + encodeURIComponent(idToken),
    { muteHttpExceptions: true }
  );
  if (res.getResponseCode() !== 200) throw new Error("Token Google tidak valid.");
  var info = JSON.parse(res.getContentText());
  var clientId = prop_("OAUTH_CLIENT_ID");
  if (clientId && info.aud !== clientId) throw new Error("Audience token Google tidak cocok.");
  if (!info.email) throw new Error("Token Google tidak memuat email.");
  cache.put(cacheKey, info.email, 1800);
  return info.email;
}

function resolveIdentity_(ident) {
  ident = ident || {};
  var email;
  if (ident.idToken) {
    email = verifyGoogleToken_(ident.idToken);
  } else if (ident.apiKey) {
    var byKey = findUserByKey_(ident.apiKey);
    if (!byKey) throw new Error("API key tidak valid. Jika tab Users masih kosong, jalankan bootstrapAdmin() di editor Apps Script untuk membuat key pertama.");
    email = byKey.email;
  } else {
    throw new Error("Autentikasi diperlukan (id_token atau api_key).");
  }

  var user = findUserByEmail_(email);
  if (!user) {
    if (ident.idToken) {
      user = appendUser_(email, "", Number(prop_("DEFAULT_QUOTA", "0")), true);
    } else {
      throw new Error("User tidak terdaftar.");
    }
  }
  if (String(user.active).toUpperCase() !== "TRUE") throw new Error("Akun nonaktif.");
  return user;
}

function checkQuota_(user) {
  var quota = Number(user.quota || 0);
  var used = Number(user.used || 0);
  if (quota > 0 && used >= quota) throw new Error("Kuota habis (" + used + "/" + quota + ").");
}

function incrementUsed_(email) {
  var sh = tab_("Users", USER_HEADERS);
  var values = sh.getDataRange().getValues();
  for (var i = 1; i < values.length; i++) {
    if (String(values[i][0]).toLowerCase() === String(email).toLowerCase()) {
      sh.getRange(i + 1, 4).setValue(Number(values[i][3] || 0) + 1);
      return;
    }
  }
}

// ------------------------------------------------------------
// Jobs
// ------------------------------------------------------------
function createJob_(user, payload) {
  checkQuota_(user);
  var country = String(payload.country || "indonesia").toLowerCase();
  var brand = String(payload.brand || "").trim();
  if (!brand) throw new Error("Brand wajib diisi.");
  if (country !== "indonesia" && country !== "philippines") throw new Error("Negara tidak dikenal.");

  var jobId = Utilities.getUuid();
  var resultTab = makeResultTab_(brand, jobId);
  var spec = {
    job_id: jobId,
    user: user.email,
    country: country,
    brand: brand,
    level: payload.level || "",
    scope: payload.scope || {},
    mode: payload.mode || "unit",
    tile: Number(payload.tile || 0),
    result_sheet: resultTab.name,
    params: payload.params || { filter_relevance: true, headless: true }
  };

  tab_("Jobs", JOB_HEADERS).appendRow([
    jobId, user.email, country, brand, JSON.stringify(spec),
    "queued", "0%", nowStr_(), "", "", "", "", "",
    resultTab.name, resultTab.gid, "", 0, 0, "", ""
  ]);
  incrementUsed_(user.email);
  try { dispatch(); } catch (e) { log_(jobId, user.email, "dispatch error: " + e); }
  return { job_id: jobId, status: "queued", result_sheet: resultTab.name };
}

function makeResultTab_(brand, jobId) {
  var ss = sheet_();
  var stamp = Utilities.formatDate(new Date(), "Asia/Jakarta", "yyyyMMdd_HHmm");
  var safe = String(brand).replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 20) || "result";
  var name = "Result_" + safe + "_" + stamp + "_" + String(jobId).slice(0, 8);
  var sh = ss.insertSheet(name);
  return { name: name, gid: sh.getSheetId() };
}

function getJob_(jobId) {
  var jobs = readTable_("Jobs", JOB_HEADERS);
  for (var i = 0; i < jobs.length; i++) {
    if (String(jobs[i].job_id) === String(jobId)) return jobs[i];
  }
  return null;
}

function listJobs_(user) {
  var jobs = readTable_("Jobs", JOB_HEADERS);
  var admin = isAdmin_(user.email);
  var out = jobs.filter(function (j) {
    return admin || String(j.user).toLowerCase() === String(user.email).toLowerCase();
  });
  out.sort(function (a, b) { return String(b.created_at).localeCompare(String(a.created_at)); });
  var sheetId = prop_("SHEET_ID");
  return out.slice(0, 100).map(function (j) {
    var gid = j.result_gid;
    var tabUrl = (j.result_sheet && gid !== "" && gid !== null && gid !== undefined)
      ? "https://docs.google.com/spreadsheets/d/" + sheetId + "/edit#gid=" + gid
      : "";
    return {
      job_id: j.job_id || "", user: j.user || "", country: j.country || "", brand: j.brand || "",
      status: j.status || "", progress: j.progress || "", created_at: fmtVal_(j.created_at),
      finished_at: fmtVal_(j.finished_at), output_url: j.output_url || "", error: fmtVal_(j.error),
      current_target: j.current_target || "", listings_found: j.listings_found || 0,
      records: j.records || 0, eta: j.eta || "", run_url: j.run_url || "",
      result_sheet: j.result_sheet || "", result_url: tabUrl
    };
  });
}

function diag_() {
  var ss = sheet_();
  var tabs = ss.getSheets().map(function (s) { return s.getName(); });
  function header(name) {
    var sh = ss.getSheetByName(name);
    if (!sh) return null;
    var cols = Math.max(1, sh.getLastColumn());
    return sh.getRange(1, 1, 1, cols).getValues()[0].map(function (v) { return String(v); });
  }
  var jobs = readTable_("Jobs", JOB_HEADERS);
  var users = readTable_("Users", USER_HEADERS);
  return {
    sheet_id: prop_("SHEET_ID"),
    max_parallel: prop_("MAX_PARALLEL"),
    admin_emails: prop_("ADMIN_EMAILS"),
    tabs: tabs,
    jobs_header: header("Jobs"),
    users_header: header("Users"),
    jobs_count: jobs.length,
    users_count: users.length
  };
}

// ------------------------------------------------------------
// Dispatch (hingga MAX_PARALLEL job berjalan bersamaan)
// ------------------------------------------------------------
function dispatch() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(20000)) return;
  try {
    var max = Number(prop_("MAX_PARALLEL", "3"));
    if (!max || max < 1) max = 1;

    var jobs = readTable_("Jobs", JOB_HEADERS);
    var active = jobs.filter(function (j) {
      var s = String(j.status).toLowerCase();
      return s === "running" || s === "dispatched";
    }).length;
    if (active >= max) return;

    var queued = jobs.filter(function (j) { return String(j.status).toLowerCase() === "queued"; });
    queued.sort(function (a, b) { return String(a.created_at).localeCompare(String(b.created_at)); });

    var slots = max - active;
    for (var i = 0; i < queued.length && i < slots; i++) {
      var job = queued[i];
      try {
        var spec = JSON.parse(job.spec_json);
        triggerWorkflow_(spec, job.job_id);
        setJobField_(job.job_id, "status", "dispatched");
      } catch (e) {
        log_(job.job_id, job.user, "dispatch gagal: " + e);
      }
    }
  } finally {
    lock.releaseLock();
  }
}

function setJobField_(jobId, field, value) {
  var sh = tab_("Jobs", JOB_HEADERS);
  var values = sh.getDataRange().getValues();
  var col = JOB_HEADERS.indexOf(field);
  if (col < 0) return;
  for (var i = 1; i < values.length; i++) {
    if (String(values[i][0]) === String(jobId)) {
      sh.getRange(i + 1, col + 1).setValue(value);
      return;
    }
  }
}

function triggerWorkflow_(spec, jobId) {
  var owner = prop_("GITHUB_OWNER");
  var repo = prop_("GITHUB_REPO");
  var pat = prop_("GITHUB_PAT");
  var ref = prop_("GITHUB_REF", "main");
  if (!owner || !repo || !pat) throw new Error("Konfigurasi GitHub belum lengkap.");
  var url = "https://api.github.com/repos/" + owner + "/" + repo +
    "/actions/workflows/scrape.yml/dispatches";
  var res = UrlFetchApp.fetch(url, {
    method: "post",
    contentType: "application/json",
    headers: {
      Authorization: "Bearer " + pat,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28"
    },
    payload: JSON.stringify({ ref: ref, inputs: { job_id: jobId, spec_json: JSON.stringify(spec) } }),
    muteHttpExceptions: true
  });
  if (res.getResponseCode() >= 300) {
    throw new Error("GitHub dispatch gagal (" + res.getResponseCode() + "): " + res.getContentText());
  }
}

function log_(jobId, user, msg) {
  try {
    tab_("Logs", LOG_HEADERS).appendRow([nowStr_(), String(jobId || ""), String(user || ""), String(msg || "")]);
  } catch (e) {}
}

// ------------------------------------------------------------
// Admin
// ------------------------------------------------------------
function isAdmin_(email) {
  var list = prop_("ADMIN_EMAILS").toLowerCase().split(",").map(function (s) { return s.trim(); });
  return list.indexOf(String(email || "").toLowerCase()) >= 0;
}

function adminCreateKey_(email, quota) {
  email = String(email || "").trim().toLowerCase();
  if (!email) throw new Error("Email wajib.");
  var key = "poi_" + Utilities.getUuid().replace(/-/g, "");
  var user = findUserByEmail_(email);
  if (user) {
    var sh = tab_("Users", USER_HEADERS);
    var values = sh.getDataRange().getValues();
    for (var i = 1; i < values.length; i++) {
      if (String(values[i][0]).toLowerCase() === email) {
        sh.getRange(i + 1, 2).setValue(hashKey_(key));
        sh.getRange(i + 1, 3).setValue(Number(quota || 0));
        sh.getRange(i + 1, 5).setValue(true);
        break;
      }
    }
  } else {
    appendUser_(email, hashKey_(key), Number(quota || 0), true);
  }
  shareSheetViewer_(email);
  return { email: email, api_key: key };
}

function shareSheetViewer_(email) {
  try {
    var ss = sheet_();
    DriveApp.getFileById(ss.getId()).addViewer(String(email).trim().toLowerCase());
  } catch (e) {
    log_("", email, "share viewer gagal: " + e);
  }
}

function adminSetActive_(email, active) {
  var sh = tab_("Users", USER_HEADERS);
  var values = sh.getDataRange().getValues();
  for (var i = 1; i < values.length; i++) {
    if (String(values[i][0]).toLowerCase() === String(email).toLowerCase()) {
      sh.getRange(i + 1, 5).setValue(!!active);
      return true;
    }
  }
  return false;
}

function adminListUsers_() {
  return readTable_("Users", USER_HEADERS).map(function (u) {
    return { email: u.email, quota: u.quota, used: u.used, active: u.active, created_at: u.created_at };
  });
}

// ------------------------------------------------------------
// Web app + API
// ------------------------------------------------------------
function doGet() {
  return HtmlService.createHtmlOutputFromFile("Index")
    .setTitle("POI Scraper Dashboard")
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

function doPost(e) {
  var out;
  try {
    var body = JSON.parse((e && e.postData && e.postData.contents) || "{}");
    out = handleAction_(body.action, body.payload || {}, {
      apiKey: body.api_key || "",
      idToken: body.id_token || ""
    }, false);
  } catch (err) {
    out = { ok: false, error: String(err && err.message ? err.message : err) };
  }
  return ContentService.createTextOutput(JSON.stringify(out)).setMimeType(ContentService.MimeType.JSON);
}

function handleAction_(action, payload, ident, isUi) {
  try {
    if (action === "options") {
      return { ok: true, data: { index_base: prop_("INDEX_BASE") } };
    }
    if (action === "config") {
      return { ok: true, data: {
        app_version: APP_VERSION,
        index_base: prop_("INDEX_BASE"),
        oauth_client_id: prop_("OAUTH_CLIENT_ID"),
        is_admin: ident && ident.idToken ? null : false
      } };
    }

    var user = resolveIdentity_(ident);

    if (action === "me") {
      return { ok: true, data: {
        email: user.email, quota: user.quota, used: user.used, is_admin: isAdmin_(user.email)
      } };
    }
    if (action === "jobs.create") {
      return { ok: true, data: createJob_(user, payload) };
    }
    if (action === "jobs.get") {
      var job = getJob_(payload.job_id);
      if (!job) throw new Error("Job tidak ditemukan.");
      if (!isAdmin_(user.email) && String(job.user).toLowerCase() !== String(user.email).toLowerCase()) {
        throw new Error("Akses ditolak.");
      }
      return { ok: true, data: job };
    }
    if (action === "jobs.list") {
      return { ok: true, data: listJobs_(user) };
    }
    if (action === "admin.users") {
      if (!isAdmin_(user.email)) throw new Error("Hanya admin.");
      return { ok: true, data: adminListUsers_() };
    }
    if (action === "admin.createKey") {
      if (!isAdmin_(user.email)) throw new Error("Hanya admin.");
      return { ok: true, data: adminCreateKey_(payload.email, payload.quota) };
    }
    if (action === "admin.setActive") {
      if (!isAdmin_(user.email)) throw new Error("Hanya admin.");
      return { ok: true, data: adminSetActive_(payload.email, payload.active) };
    }
    if (action === "admin.diag") {
      if (!isAdmin_(user.email)) throw new Error("Hanya admin.");
      return { ok: true, data: diag_() };
    }
    throw new Error("Action tidak dikenal: " + action);
  } catch (err) {
    var msg = err && err.message ? err.message : String(err);
    if (err && err.stack) {
      msg += " | " + String(err.stack).split("\n").slice(0, 2).join(" ").trim();
    }
    return { ok: false, error: msg };
  }
}

// Wrapper untuk google.script.run dari dashboard
function uiCall(action, payload, ident) {
  return handleAction_(action, payload || {}, ident || {}, true);
}

// Endpoint khusus daftar job (fallback bila routing action bermasalah).
function uiJobsList(ident) {
  try {
    var user = resolveIdentity_(ident || {});
    return { ok: true, data: listJobs_(user), app_version: APP_VERSION };
  } catch (e) {
    return { ok: false, error: "uiJobsList: " + String(e && e.message ? e.message : e) };
  }
}

// Diagnostik cepat dari editor: uji listJobs_ tanpa lewat dashboard.
function testListJobs() {
  var users = readTable_("Users", USER_HEADERS);
  var email = users.length ? users[0].email : String(prop_("ADMIN_EMAILS", "")).split(",")[0];
  var out = listJobs_({ email: email });
  Logger.log("APP_VERSION=" + APP_VERSION);
  Logger.log("jobs for " + email + ": " + out.length);
  Logger.log(JSON.stringify(out).slice(0, 1500));
  return out;
}

// ------------------------------------------------------------
// Setup (jalankan sekali dari editor)
// ------------------------------------------------------------
function syncHeaders_(name, headers) {
  var sh = tab_(name, headers);
  sh.getRange(1, 1, 1, headers.length).setValues([headers]);
}

function setup() {
  tab_("Users", USER_HEADERS);
  tab_("Jobs", JOB_HEADERS);
  tab_("Logs", LOG_HEADERS);
  syncHeaders_("Users", USER_HEADERS);
  syncHeaders_("Jobs", JOB_HEADERS);
  syncHeaders_("Logs", LOG_HEADERS);
  var p = PropertiesService.getScriptProperties();
  var defaults = {
    GITHUB_OWNER: "RamaIdsan",
    GITHUB_REPO: "poi-scraper",
    GITHUB_REF: "main",
    INDEX_BASE: "https://raw.githubusercontent.com/RamaIdsan/poi-scraper/main/admin",
    ADMIN_EMAILS: "ramaidsan9995@gmail.com",
    DEFAULT_QUOTA: "0",
    MAX_PARALLEL: "3"
  };
  Object.keys(defaults).forEach(function (k) {
    if (!p.getProperty(k)) p.setProperty(k, defaults[k]);
  });
  Logger.log("Setup selesai. Sheet ID terdeteksi: " + (prop_("SHEET_ID") || "(belum diset)"));
}

function setupTriggers() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === "dispatch") ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger("dispatch").timeBased().everyMinutes(1).create();
  Logger.log("Trigger dispatch tiap menit dipasang.");
}

function setSheetId(id) {
  PropertiesService.getScriptProperties().setProperty("SHEET_ID", String(id).trim());
  Logger.log("SHEET_ID diset.");
}

function setAdminEmails(emails) {
  PropertiesService.getScriptProperties().setProperty("ADMIN_EMAILS", String(emails).trim());
  Logger.log("ADMIN_EMAILS = " + emails);
}

function setMaxParallel(n) {
  n = Number(n);
  if (!n || n < 1) n = 1;
  PropertiesService.getScriptProperties().setProperty("MAX_PARALLEL", String(n));
  Logger.log("MAX_PARALLEL = " + n);
  return n;
}

/**
 * Bootstrap user admin pertama (dijalankan sekali dari editor Apps Script).
 * Membuat/reset API key untuk email admin, lalu mencetaknya ke Execution log.
 * Salin "API KEY: ..." dan tempel di kolom API key dashboard.
 */
function bootstrapAdmin() {
  tab_("Users", USER_HEADERS);
  tab_("Jobs", JOB_HEADERS);
  tab_("Logs", LOG_HEADERS);
  syncHeaders_("Users", USER_HEADERS);
  syncHeaders_("Jobs", JOB_HEADERS);
  syncHeaders_("Logs", LOG_HEADERS);

  var email = String(prop_("ADMIN_EMAILS", "ramaidsan9995@gmail.com")).split(",")[0].trim();
  if (!email) throw new Error("ADMIN_EMAILS kosong. Jalankan setAdminEmails('ramaidsan9995@gmail.com') dulu.");

  var res = adminCreateKey_(email, 0);
  Logger.log("============================================================");
  Logger.log("ADMIN   : " + res.email);
  Logger.log("API KEY : " + res.api_key);
  Logger.log("Simpan key ini (hanya tampil di log). Tempel ke dashboard.");
  Logger.log("============================================================");
  return res.api_key;
}

/** Hash sebuah API key (untuk debug / verifikasi manual). */
function hashApiKey(key) {
  var h = hashKey_(key);
  Logger.log("hash(" + key + ") = " + h);
  return h;
}

/** Cek user terdaftar atau belum. */
function listApiUsers() {
  var users = readTable_("Users", USER_HEADERS);
  Logger.log("Total users: " + users.length);
  users.forEach(function (u) {
    Logger.log("- " + u.email + " | quota=" + u.quota + " | used=" + u.used + " | active=" + u.active);
  });
  return users;
}
