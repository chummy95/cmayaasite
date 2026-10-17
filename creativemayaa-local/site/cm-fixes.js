/*
 * Creative Mayaa – static-site fixes
 *
 * The site is a static copy of the old WordPress/Elementor build. Elementor Pro's
 * popup module (which opened the hamburger menu) can't run without WordPress, so
 * this small script recreates that behaviour: any "open popup" link shows the
 * matching popup in a full-screen overlay, and the close button / Esc / clicking
 * a menu link closes it again.
 */
(function () {
  'use strict';

  var ACTION_PREFIX = '#elementor-action';

  function decodeAction(href) {
    // href looks like "#elementor-action%3Aaction%3Dpopup%3Aopen%26settings%3D<base64>"
    try {
      var decoded = decodeURIComponent(href.slice(href.indexOf(ACTION_PREFIX) + 1));
      var match = decoded.match(/action=([^&]+)(?:&settings=(.+))?/);
      if (!match) return null;
      var settings = {};
      if (match[2]) {
        try { settings = JSON.parse(atob(match[2])); } catch (e) { settings = {}; }
      }
      return { action: match[1], settings: settings };
    } catch (e) {
      return null;
    }
  }

  function injectStyles() {
    if (document.getElementById('cm-popup-styles')) return;
    var css = [
      '.cm-popup.elementor-popup-modal{position:fixed;inset:0;width:100%;height:100%;z-index:99999;display:none;pointer-events:all;}',
      '.cm-popup.elementor-popup-modal.cm-open{display:flex;}',
      '.cm-popup .dialog-widget-content{position:relative;margin:0;animation:cmFadeIn .6s ease both;}',
      '.cm-popup .dialog-message{box-sizing:border-box;}',
      '.cm-popup .dialog-close-button{position:absolute;display:flex;align-items:center;justify-content:center;',
      'width:2.4em;height:2.4em;border-radius:50%;cursor:pointer;border:0;line-height:1;z-index:10000;}',
      '.cm-popup.elementor-popup-modal .dialog-close-button{top:24px !important;right:24px !important;left:auto !important;font-size:18px !important;}',
      '.cm-popup .dialog-close-button svg{width:18px !important;height:18px !important;}',
      '.cm-popup [data-elementor-type=popup]{display:block !important;}',
      '.cm-popup .elementor-invisible{visibility:visible !important;}',
      'html.cm-no-scroll,html.cm-no-scroll body{overflow:hidden !important;}',
      '@keyframes cmFadeIn{from{opacity:0}to{opacity:1}}'
    ].join('');
    var style = document.createElement('style');
    style.id = 'cm-popup-styles';
    style.textContent = css;
    document.head.appendChild(style);
  }

  var modals = {};
  var lastTrigger = null;

  function buildModal(id) {
    if (modals[id]) return modals[id];
    var source = document.querySelector('[data-elementor-type="popup"][data-elementor-id="' + id + '"]');
    if (!source) return null;

    var settings = {};
    try { settings = JSON.parse(source.getAttribute('data-elementor-settings') || '{}'); } catch (e) {}

    var modal = document.createElement('div');
    modal.id = 'elementor-popup-modal-' + id;
    modal.className = 'cm-popup dialog-widget dialog-lightbox-widget dialog-type-buttons dialog-type-lightbox elementor-popup-modal';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-label', 'Menu');

    var content = document.createElement('div');
    content.className = 'dialog-widget-content dialog-lightbox-widget-content';
    if (settings.entrance_animation_duration && settings.entrance_animation_duration.size) {
      content.style.animationDuration = settings.entrance_animation_duration.size + 's';
    }

    var close = document.createElement('button');
    close.type = 'button';
    close.className = 'dialog-close-button dialog-lightbox-close-button';
    close.setAttribute('aria-label', 'Close menu');
    close.innerHTML = '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M18.3 5.7a1 1 0 0 0-1.4 0L12 10.6 7.1 5.7a1 1 0 1 0-1.4 1.4L10.6 12l-4.9 4.9a1 1 0 1 0 1.4 1.4l4.9-4.9 4.9 4.9a1 1 0 0 0 1.4-1.4L13.4 12l4.9-4.9a1 1 0 0 0 0-1.4z"/></svg>';
    close.addEventListener('click', function () { closeModal(id); });

    var message = document.createElement('div');
    message.className = 'dialog-message dialog-lightbox-message';
    message.appendChild(source); // move the popup markup into the overlay

    content.appendChild(close);
    content.appendChild(message);
    modal.appendChild(content);
    document.body.appendChild(modal);

    // Clicking the dark backdrop (outside the panel) closes the menu.
    modal.addEventListener('click', function (e) {
      if (e.target === modal) closeModal(id);
    });
    // Following a real link inside the menu closes it.
    message.addEventListener('click', function (e) {
      var link = e.target.closest('a[href]');
      if (link && link.getAttribute('href').indexOf(ACTION_PREFIX) === -1) closeModal(id);
    });

    modals[id] = { el: modal, settings: settings, close: close };
    return modals[id];
  }

  function openModal(id, trigger) {
    var m = buildModal(id);
    if (!m) return;
    lastTrigger = trigger || null;
    m.el.classList.add('cm-open');
    if (m.settings.prevent_scroll === 'yes') document.documentElement.classList.add('cm-no-scroll');
    m.close.focus({ preventScroll: true });
  }

  function closeModal(id) {
    var ids = id ? [id] : Object.keys(modals);
    ids.forEach(function (key) {
      if (modals[key]) modals[key].el.classList.remove('cm-open');
    });
    document.documentElement.classList.remove('cm-no-scroll');
    if (lastTrigger) { try { lastTrigger.focus({ preventScroll: true }); } catch (e) {} }
  }

  function toggleModal(id, trigger) {
    var m = modals[id];
    if (m && m.el.classList.contains('cm-open')) closeModal(id);
    else openModal(id, trigger);
  }

  document.addEventListener('click', function (e) {
    var link = e.target.closest && e.target.closest('a[href*="elementor-action"]');
    if (!link) return;
    var data = decodeAction(link.getAttribute('href'));
    if (!data) return;

    if (data.action === 'popup:open' && data.settings.id) {
      e.preventDefault();
      e.stopImmediatePropagation();
      if (data.settings.toggle) toggleModal(String(data.settings.id), link);
      else openModal(String(data.settings.id), link);
    } else if (data.action === 'popup:close') {
      e.preventDefault();
      e.stopImmediatePropagation();
      closeModal();
    }
  }, true);

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeModal();
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', injectStyles);
  } else {
    injectStyles();
  }
})();
