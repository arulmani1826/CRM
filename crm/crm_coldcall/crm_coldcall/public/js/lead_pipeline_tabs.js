/**
 * Cold-Call CRM — Desk helpers (scopes §2 + §4, features 2 + 3).
 *
 * What this file does:
 *  1. PIPELINE TABS — on the CRM Lead list, injects 4 colour-coded shortcut
 *     buttons (🟢 Interested / 🔴 Not Interested / 🟡 Follow-up / 🔵 No Answer)
 *     that filter the list by status and show live counts from the server.
 *  2. RECORDING HARDENING — on the CRM Call Log form, hides the
 *     `recording_url` field + any audio player for non-admins. The server
 *     still blocks the API, so this is UX-only (defence in depth).
 *     Recording itself is AUTOMATIC for every call — agents never press
 *     record (see BLOCK 4 banner).
 *  3. PDF BUTTON — on the CRM Lead form, adds a "Parse PDF" button that calls
 *     `crm_coldcall.api.pdf_import.parse_and_map` with the attached file.
 *  4. AUTO-LOG — on the CRM Call Log form, fetches pre-filled defaults from
 *     `get_call_log_defaults` so the officer only picks Call Disposition,
 *     and shows an "Auto-log + Auto-recording ON" banner.
 *  5. RBAC UI — for Agents, hides Admin-disabled modules in nav plus
 *     Create/Delete buttons (backend has_permission + guard_create still
 *     enforce every request with 403 + audit rows).
 *
 * Loaded via hooks.app_include_js on every Desk page. Every block checks the
 * current route + user roles first, so Admins and unrelated pages are
 * untouched. Edit one block at a time — they share only the helpers below.
 */

(function () {
  'use strict';

  // -------------------------------------------------------------------------
  // Helpers (shared, side-effect free)
  // -------------------------------------------------------------------------

  /** True when the session user is an admin (sees recordings, deletes). */
  function isAdmin() {
    try {
      return (
        frappe.user.has_role('Administrator') ||
        frappe.user.has_role('System Manager') ||
        frappe.user.has_role('Sales Manager')
      );
    } catch (e) {
      return false;
    }
  }

  /** 4 pipeline modes with colours matching the server seed. */
  const MODES = [
    { name: 'Interested', emoji: '🟢', color: '#16a34a' },
    { name: 'Not Interested', emoji: '🔴', color: '#dc2626' },
    { name: 'Follow-up', emoji: '🟡', color: '#ca8a04' },
    { name: 'No Answer', emoji: '🔵', color: '#2563eb' },
  ];

  // -------------------------------------------------------------------------
  // BLOCK 1: Pipeline tabs on the CRM Lead list
  // -------------------------------------------------------------------------

  function injectPipelineTabs() {
    // Only run on the Lead list view.
    const route = (frappe.get_route() || []).join('/');
    if (route !== 'List/CRM Lead/List' && !route.startsWith('List/CRM Lead')) return;
    // Avoid double-injection on re-render.
    if (document.getElementById('coldcall-pipeline-tabs')) return;

    const listHeader = document.querySelector('.list-row-head, .frappe-list .page-head');
    const anchor = document.querySelector('.frappe-list .result, .frappe-list .list-filters')
      || document.querySelector('.page-head');
    if (!anchor || !anchor.parentNode) return;

    const bar = document.createElement('div');
    bar.id = 'coldcall-pipeline-tabs';
    bar.style.cssText =
      'display:flex;gap:8px;flex-wrap:wrap;margin:8px 0;padding:8px 12px;' +
      'background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;';

    // "All" reset button first.
    const allBtn = document.createElement('button');
    allBtn.className = 'btn btn-xs btn-default';
    allBtn.textContent = '⬜ All';
    allBtn.onclick = () => frappe.set_route('List', 'CRM Lead', 'List');
    bar.appendChild(allBtn);

    MODES.forEach((m) => {
      const b = document.createElement('button');
      b.className = 'btn btn-xs btn-default';
      b.dataset.mode = m.name;
      b.innerHTML = `${m.emoji} ${m.name} <span class="badge" data-count>…</span>`;
      b.style.borderLeft = `4px solid ${m.color}`;
      b.onclick = () => {
        // Standard list filter — no custom backend needed.
        frappe.set_route('List', 'CRM Lead', 'List', { status: m.name });
      };
      bar.appendChild(b);
    });

    anchor.parentNode.insertBefore(bar, anchor);

    // Fill live counts (best-effort; tabs still work if the call fails).
    frappe
      .call('crm_coldcall.api.lead_pipeline.get_pipeline_counts')
      .then((r) => {
        const counts = (r && r.message && r.message.counts) || {};
        bar.querySelectorAll('button[data-mode]').forEach((btn) => {
          const c = counts[btn.dataset.mode];
          const badge = btn.querySelector('[data-count]');
          if (badge) badge.textContent = c != null ? `(${c})` : '';
        });
      })
      .catch(() => {});
  }

  // -------------------------------------------------------------------------
  // BLOCK 2: Hide recording controls from Agents (CRM Call Log form)
  // -------------------------------------------------------------------------

  function hardenCallLogForm() {
    if (isAdmin()) return; // Admins keep full visibility.
    const route = frappe.get_route() || [];
    // Form route looks like ['Form', 'CRM Call Log', '<name>'].
    if (route[0] !== 'Form' || route[1] !== 'CRM Call Log') return;
    // Hide any element bound to recording_url + audio players.
    const selectors = [
      '[data-fieldname="recording_url"]',
      'audio[src*="call-record"]',
      'a[href*="call-record"]',
    ];
    selectors.forEach((sel) => {
      document.querySelectorAll(sel).forEach((el) => {
        const row = el.closest('.frappe-control, .form-group') || el;
        row.style.display = 'none';
      });
    });
  }

  // -------------------------------------------------------------------------
  // BLOCK 3: "Parse PDF" button on the CRM Lead form
  // -------------------------------------------------------------------------

  function addPdfButton() {
    const route = frappe.get_route() || [];
    if (route[0] !== 'Form' || route[1] !== 'CRM Lead') return;
    if (document.getElementById('coldcall-parse-pdf')) return;
    const toolbar = document.querySelector('.form-actions, .page-actions');
    if (!toolbar) return;
    const btn = document.createElement('button');
    btn.id = 'coldcall-parse-pdf';
    btn.className = 'btn btn-xs btn-primary';
    btn.style.marginLeft = '8px';
    btn.textContent = '📄 Parse PDF → Fill Lead';
    btn.onclick = () => {
      const doc = cur_frm && cur_frm.doc;
      if (!doc || !doc.custom_pdf) {
        frappe.msgprint('Attach a PDF in “Historical Data PDF” first.');
        return;
      }
      frappe.call({
        method: 'crm_coldcall.api.pdf_import.parse_and_map',
        args: { lead_name: doc.name, file_url: doc.custom_pdf },
        freeze: true,
        freeze_message: 'Parsing PDF…',
        callback: (r) => {
          frappe.msgprint(
            `Mapped: <pre>${frappe.utils.escape_html(
              JSON.stringify(r.message, null, 2)
            )}</pre>`
          );
          cur_frm.reload_doc();
        },
      });
    };
    toolbar.appendChild(btn);
  }

  // -------------------------------------------------------------------------
  // BLOCK 4: Auto-log + auto-recording UX (CRM Call Log form, features 2+3)
  // -------------------------------------------------------------------------

  function autoLogCallLogForm() {
    const route = frappe.get_route() || [];
    if (route[0] !== 'Form' || route[1] !== 'CRM Call Log') return;
    if (document.getElementById('coldcall-autolog-banner')) return;
    const form = document.querySelector('.form-layout, .form-page, .layout-main');
    if (!form || !form.parentNode) return;

    const banner = document.createElement('div');
    banner.id = 'coldcall-autolog-banner';
    banner.style.cssText =
      'display:flex;gap:8px;align-items:center;margin:8px 0;padding:8px 12px;' +
      'background:#ecfdf5;border:1px solid #6ee7b7;border-radius:8px;font-size:12px;';
    banner.innerHTML =
      '⚡ <b>Auto-log ON</b> — details pre-filled, just pick ' +
      '<b>Call Disposition</b> &nbsp;|&nbsp; 🔴 <b>Auto-recording ON</b> — no manual activation needed.';
    form.parentNode.insertBefore(banner, form);

    // Pre-fill a NEW form from the server so the officer never types
    // numbers/timestamps by hand (server hook backfills anything missing).
    try {
      if (window.cur_frm && cur_frm.is_new && cur_frm.is_new() && cur_frm.doc) {
        const d = cur_frm.doc;
        if (!d.to || !d.from) {
          frappe
            .call({
              method: 'crm_coldcall.api.call_recording.get_call_log_defaults',
              args: {
                reference_doctype: d.reference_doctype || 'CRM Lead',
                reference_docname: d.reference_docname || null,
              },
            })
            .then((r) => {
              const vals = (r && r.message) || {};
              Object.keys(vals).forEach((k) => {
                if (vals[k] != null && (cur_frm.doc[k] == null || cur_frm.doc[k] === '')) {
                  cur_frm.set_value(k, vals[k]);
                }
              });
            })
            .catch(() => {});
        }
      }
    } catch (e) {
      console.warn('[crm_coldcall] auto-log defaults skipped', e);
    }
  }

  // -------------------------------------------------------------------------
  // BLOCK 5: Role-based UI for Agents (RBAC spec §6 + §7).
  // -------------------------------------------------------------------------
  // Backend (has_permission + guard_create) is the real enforcement — this
  // block only hides unauthorised modules/buttons so Agents are not
  // offered actions that would 403. Admins are untouched.

  /** Module key -> Desk list route fragment used for nav matching. */
  const RBAC_MODULE_ROUTES = {
    leads: ['CRM Lead'],
    tasks: ['CRM Task'],
    deals: ['CRM Deal'],
    contacts: ['CRM Contacts', 'Contact'],
    organizations: ['CRM Organization'],
    call_logs: ['CRM Call Log'],
  };

  /** True when a button label is a Convert action (must stay for Agents). */
  function isConvertAction(label) {
    return /convert/i.test(label || '');
  }

  let rbacState = null; // {is_admin, is_agent, modules:{...}} from server

  function loadRbacState() {
    frappe
      .call('crm_coldcall.api.agent_access.get_my_modules')
      .then((r) => {
        rbacState = (r && r.message) || null;
        applyRbacUi();
      })
      .catch(() => {});
  }

  function rbacHideEl(el) {
    if (el && el.style) el.style.display = 'none';
  }

  function applyRbacUi() {
    if (!rbacState || rbacState.is_admin) return; // Admins see everything.
    const mods = rbacState.modules || {};

    // --- 5a. Hide Dashboard + disabled modules for Agents ----------------
    // Dashboard is never shown to Agents (final RBAC matrix).
    document
      .querySelectorAll('.desk-sidebar a, .sidebar-menu a, [data-route]')
      .forEach((a) => {
        const text = (a.textContent || '').trim().toLowerCase();
        const route = (a.getAttribute('data-route') || '').toLowerCase();
        if (
          text === 'dashboard' ||
          text.includes('dashboard') ||
          route.includes('dashboard')
        ) {
          rbacHideEl(a.closest('li') || a);
        }
      });
    const disabled = Object.keys(RBAC_MODULE_ROUTES).filter(
      (m) => mods[m] && !mods[m].view
    );
    if (disabled.length) {
      const labels = [];
      disabled.forEach((m) => labels.push(...RBAC_MODULE_ROUTES[m]));
      document
        .querySelectorAll('.desk-sidebar a, .sidebar-menu a, [data-route]')
        .forEach((a) => {
          const text = (a.textContent || '').trim().toLowerCase();
          const route = (a.getAttribute('data-route') || '').toLowerCase();
          if (
            labels.some(
              (l) =>
                text.includes(l.toLowerCase()) || route.includes(l.toLowerCase())
            )
          ) {
            rbacHideEl(a.closest('li') || a);
          }
        });
    }

    // --- 5b. Hide Create / Delete buttons for Agents ----------------------
    // "Convert Deal" actions are explicitly kept (spec §6).
    // Notes keep their Create button (agents must be able to add notes);
    // Call Logs keep their Create button (agents must be able to log calls).
    // Delete stays hidden everywhere.
    const route = frappe.get_route() || [];
    const isNotes =
      route[1] === 'FCRM Note' || route[1] === 'Notes';
    const isCallLog =
      route[1] === 'CRM Call Log' || route[1] === 'Call Logs';
    const keepCreate = isNotes || isCallLog;
    if (!keepCreate) {
      // List "New" buttons.
      document
        .querySelectorAll('.page-actions .btn-primary, .list-actions .btn-primary')
        .forEach((btn) => {
          const label = (btn.textContent || '').trim();
          if (/^(new|create|add)\b/i.test(label) && !isConvertAction(label)) {
            rbacHideEl(btn);
          }
        });
      // "New" navbar button.
      document.querySelectorAll('button[data-label="New"]').forEach(rbacHideEl);
    }
    // Bulk-action Delete + row three-dot menu entries.
    // Agents also lose Import/Export everywhere (final matrix).
    document.querySelectorAll('.list-bulk-actions .btn-danger').forEach(rbacHideEl);
    document
      .querySelectorAll('.dropdown-menu a, .menu-item')
      .forEach((a) => {
        const label = a.textContent || '';
        if (isConvertAction(label)) return; // Convert to Deal stays
        if (/^\s*delete\s*$/i.test(label)) rbacHideEl(a);
        else if (/^\s*(import|export|download|upload)\b/i.test(label)) rbacHideEl(a);
      });
    // Form Delete (danger) buttons.
    if (route[0] === 'Form') {
      document
        .querySelectorAll('.form-actions .btn-danger, .page-actions .btn-danger')
        .forEach((btn) => {
          if (!isConvertAction(btn.textContent)) rbacHideEl(btn);
        });
    }
  }

  // ---------------------------------------------------------------------------
  // Boot: re-run on every route change (Frappe is a SPA).
  // ---------------------------------------------------------------------------

  function boot() {
    try {
      injectPipelineTabs();
      hardenCallLogForm();
      addPdfButton();
      autoLogCallLogForm();
      applyRbacUi();
    } catch (e) {
      console.warn('[crm_coldcall] desk helper error', e);
    }
  }

  // Frappe SPA navigation events.
  if (window.frappe && frappe.router && frappe.router.on) {
    frappe.router.on('change', () => {
      setTimeout(boot, 300);
      setTimeout(applyRbacUi, 1200); // late pass after async list render
    });
  }
  document.addEventListener('DOMContentLoaded', () => {
    setTimeout(boot, 500);
    loadRbacState();
  });
  // First paint (script loads after Desk boot).
  setTimeout(boot, 800);
  setTimeout(loadRbacState, 1000);
})();
