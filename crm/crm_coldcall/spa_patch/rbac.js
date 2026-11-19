/**
 * Cold-Call RBAC overlay for the Frappe CRM SPA (UX layer).
 *
 * Source of truth: /crm_coldcall/spa_patch/rbac.js in the crm_coldcall app.
 * Applied to the container's CRM frontend by apply_spa_patch.sh, then the
 * frontend is rebuilt. Re-apply after every CRM app reinstall/update.
 *
 * Agents (Admins untouched except Assign-hiding, which is global):
 *  1. Redirects agents away from Dashboard + Data Import to first allowed module.
 *  2. Hides sidebar nav entries for Dashboard + Admin-disabled modules.
 *  3. Hides Create/New/Add + Delete + Import/Export buttons and menu entries
 *     everywhere, EXCEPT Convert actions, Notes-Create and Call-Log-Create.
 *  4. Hides Assign controls (Assign/Assigned To/Lead Owner) for EVERYONE —
 *     visibility is view-permission based, assignment UI is retired.
 *  5. Hides the list-toolbar "..." (More Options) menu for agents — its only
 *     items are Import/Export (backend-blocked -> 403 glitch) and Customize
 *     Quick Filters (manager-only), i.e. an empty menu for agents.
 *  6. Safety net: if the sidebar user dropdown renders empty (FCRM Settings
 *     unreadable -> no dropdown_items), injects a working "Log out" item so
 *     agents are never locked in. Backend grants read in install.py.
 *
 * Backend (has_permission + guard_create + export DocPerm in crm_coldcall)
 * enforces every request with 403 + audit rows — this file is UX-only and
 * fail-open: if the state endpoint is unreachable, nothing is hidden.
 */

const ENDPOINT = '/api/method/crm_coldcall.api.agent_access.get_my_modules'

// Route names in the CRM SPA mapped to our module keys.
const MODULE_TO_ROUTE = {
  leads: 'Leads',
  tasks: 'Tasks',
  deals: 'Deals',
  contacts: 'Contacts',
  organizations: 'Organizations',
  call_logs: 'Call Logs',
}

// Sidebar labels (lowercase) per module key. Dashboard is always hidden.
const MODULE_LABELS = {
  leads: ['leads'],
  tasks: ['tasks'],
  deals: ['deals'],
  contacts: ['contacts'],
  organizations: ['organizations', 'organization'],
  call_logs: ['call logs', 'call log'],
}

let state = null
let appliedRedirect = false

async function loadState() {
  try {
    const res = await fetch(ENDPOINT, { credentials: 'same-origin' })
    const data = await res.json()
    state = (data && data.message) || null
  } catch (e) {
    state = null
  }
  return state
}

function isAgent() {
  return !!(state && !state.is_admin)
}

function firstAllowedPath() {
  const mods = (state && state.modules) || {}
  const order = ['leads', 'tasks', 'deals', 'contacts', 'organizations', 'call_logs']
  const base = {
    leads: '/crm/leads',
    tasks: '/crm/tasks',
    deals: '/crm/deals',
    contacts: '/crm/contacts',
    organizations: '/crm/organizations',
    call_logs: '/crm/call-logs',
  }
  for (const m of order) {
    if (mods[m] && mods[m].view) return base[m]
  }
  return '/crm/leads'
}

function hideEl(el) {
  if (el && el.style && el.style.display !== 'none') el.style.display = 'none'
}

function isConvert(label) {
  return /convert/i.test(label || '')
}

function isAssignControl(label) {
  return /^(assign|assigned(\s+to)?|lead\s+owner)\b/i.test((label || '').trim())
}

function isImportExport(label) {
  return /^(import|export|download|upload)\b/i.test((label || '').trim())
}

// --- Assign-hiding runs for EVERYONE (retired UI, not a permission) --------
function hideAssignControls() {
  document.querySelectorAll('a, button, label, span').forEach((el) => {
    // Only leaf-ish controls to avoid hiding whole panels by text match.
    if (!/^(A|BUTTON|LABEL)$/.test(el.tagName)) return
    const label = (el.textContent || '').trim()
    if (label && label.length < 40 && isAssignControl(label)) {
      hideEl(el.closest('div[class*="flex"]') || el)
    }
  })
}

// --- Hide the list-toolbar "..." (More Options) menu for Agents -------------
// Its items are Import / Export / Customize Quick Filters: Import & Export
// are backend-blocked for Agents (403 toast glitch) and Customize is
// manager-only, so for Agents the menu opens empty/broken -> remove it.
// Selector: an icon-only ellipsis trigger whose enclosing ViewControls
// toolbar cluster contains the always-rendered Refresh icon
// (lucide-refresh-ccw). Climb capped at 3 parents so page-header,
// bulk-select-banner and card "..."/Convert menus are never touched.
function hideToolbarMoreOptions() {
  document.querySelectorAll('button[aria-haspopup="menu"]').forEach((btn) => {
    if ((btn.textContent || '').trim()) return // labelled menus kept
    if (!/lucide-more-horizontal/.test(btn.innerHTML || '')) return
    let node = btn.parentElement
    for (let i = 0; i < 3 && node; i++) {
      if (/lucide-refresh-ccw/.test(node.innerHTML || '')) {
        hideEl(btn)
        return
      }
      node = node.parentElement
    }
  })
}

// --- Fallback: agents must always have a working Log out entry -------------
// The CRM SPA builds the sidebar user menu (brand + name, bottom-left)
// entirely from FCRM Settings.dropdown_items. A role without read on that
// DocType gets an EMPTY menu -> no "My Settings" and NO "Log out". The
// backend now grants agents read (install.py: ensure_agent_ui_settings_
// read); this is the UX safety net: any empty dropdown menu anchored in
// the sidebar gets a single "Log out" item wired to the standard logout
// API. No-op for Admins and whenever the menu already has items.
function ensureAgentLogoutFallback() {
  document.querySelectorAll('.dropdown-menu, [role="menu"]').forEach((menu) => {
    if (menu.querySelector('[role="menuitem"], a, button')) return // has items
    if (!menu.closest('aside, nav, [role="navigation"]')) return // sidebar only
    if (menu.querySelector('[data-rbac-logout]')) return // already patched
    const item = document.createElement('button')
    item.setAttribute('role', 'menuitem')
    item.setAttribute('data-rbac-logout', '1')
    item.className =
      'flex h-7 w-full items-center gap-2 rounded px-2 text-left ' +
      'text-base text-ink-gray-7 hover:bg-surface-gray-2'
    item.textContent = 'Log out'
    item.addEventListener('click', () => {
      fetch('/api/method/logout', { method: 'POST', credentials: 'same-origin' }).finally(
        () => {
          window.location.href = '/login'
        },
      )
    })
    menu.appendChild(item)
  })
}

function pass() {
  // Assign UI is retired globally (spec: no Assign Agent anywhere).
  try {
    hideAssignControls()
  } catch (e) {
    /* best-effort only */
  }
  if (!isAgent()) return
  const mods = state.modules || {}

  // --- 1. Redirect away from Dashboard + Data Import ------------------------
  const path = window.location.pathname.replace(/\/+$/, '') || '/'
  if (
    !appliedRedirect &&
    (path === '/crm/dashboard' ||
      path === '/crm' ||
      path === '/crm/data-import' ||
      path.startsWith('/crm/data-import/'))
  ) {
    appliedRedirect = true
    window.location.replace(firstAllowedPath())
    return
  }

  // --- 2. Hide Dashboard + disabled-module sidebar entries -----------------
  const hiddenLabels = ['dashboard']
  Object.keys(MODULE_LABELS).forEach((m) => {
    if (mods[m] && !mods[m].view) hiddenLabels.push(...MODULE_LABELS[m])
  })
  document.querySelectorAll('nav a, aside a, [role="navigation"] a').forEach((a) => {
    const text = (a.textContent || '').trim().toLowerCase()
    if (hiddenLabels.some((l) => text === l || text.startsWith(l + ' '))) {
      hideEl(a.closest('li') || a)
    }
  })

  // --- 3. Hide Create / Delete / Import / Export (keep Convert) --------
  // Notes keep their Create button (agents must be able to add notes);
  // Call Logs keep their Create button (agents must be able to log calls).
  // Delete + Import/Export stay hidden everywhere for Agents.
  const isNotes = path === '/crm/notes' || path.startsWith('/crm/notes/')
  const isCallLogs = path === '/crm/call-logs' || path.startsWith('/crm/call-logs/')
  const keepCreate = isNotes || isCallLogs
  if (!keepCreate) {
    document.querySelectorAll('button').forEach((btn) => {
      const label = (btn.textContent || '').trim()
      if (!label || isConvert(label)) return
      if (/^(new|create|add|delete|import|export|download|upload)\b/i.test(label)) hideEl(btn)
    })
  } else {
    // On Notes / Call Logs pages: still hide Delete + Import/Export.
    document.querySelectorAll('button').forEach((btn) => {
      const label = (btn.textContent || '').trim()
      if (!label || isConvert(label)) return
      if (/^(delete|import|export|download|upload)\b/i.test(label)) hideEl(btn)
    })
  }
  document
    .querySelectorAll('[role="menuitem"], .dropdown-menu a, [role="menu"] button')
    .forEach((item) => {
      const label = (item.textContent || '').trim()
      if (!label || isConvert(label)) return
      if (keepCreate && /^(new|create|add)\b/i.test(label)) return // keep Create
      if (/^(new|create|add|delete|import|export|download|upload)\b/i.test(label)) hideEl(item)
    })

  // --- 4. Remove the toolbar "..." menu (Import/Export glitch) --------------
  try {
    hideToolbarMoreOptions()
  } catch (e) {
    /* best-effort only */
  }

  // --- 5. Guarantee a working Log out entry in the sidebar user menu -------
  // Safety net for the FCRM Settings read issue: if the user dropdown
  // renders empty (no dropdown_items loaded), agents would have no way
  // to log out. Inject a single "Log out" item into that empty menu.
  try {
    ensureAgentLogoutFallback()
  } catch (e) {
    /* best-effort only */
  }

  // Icon-only Delete safety net (detail-page trash, row trash buttons):
  // no text to regex-match, so key off aria-label/title/trash icon markup.
  document.querySelectorAll('button').forEach((btn) => {
    if ((btn.textContent || '').trim()) return // text handled above
    const hint = (
      (btn.getAttribute('aria-label') || '') +
      ' ' +
      (btn.getAttribute('title') || '')
    ).toLowerCase()
    const html = btn.innerHTML || ''
    if (/delete/.test(hint) || /lucide-trash|trash-2/.test(html)) hideEl(btn)
  })
}

let scheduled = false
function schedule() {
  if (scheduled) return
  scheduled = true
  setTimeout(() => {
    scheduled = false
    try {
      pass()
    } catch (e) {
      /* best-effort only */
    }
  }, 250)
}

export function initRbacUi() {
  loadState().then(() => {
    // Glitch-proofing: re-run on DOM churn, route changes AND a slow
    // interval, so hidden buttons never reappear after refresh/navigation.
    schedule()
    setInterval(() => {
      try {
        pass()
      } catch (e) {
        /* best-effort only */
      }
    }, 3000)
    if (!isAgent()) return
    // SPA renders async + navigates client-side: re-run on DOM churn.
    const observer = new MutationObserver(schedule)
    observer.observe(document.documentElement, { childList: true, subtree: true })
    window.addEventListener('popstate', schedule)
    // hash/history pushes without popstate (vue-router push):
    const origPush = window.history.pushState
    window.history.pushState = function (...args) {
      const r = origPush.apply(this, args)
      schedule()
      return r
    }
  })
}
