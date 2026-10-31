"""K2 MOTOR — SITE + DEVIS VEHICULE (UN SEUL FICHIER A MODIFIER).

Installation : remplacez seulement app.py sur GitHub.
Conservez les fichiers existants (public/index.html, public/app.js,
public/styles.css, services.json), Render et Neon.

ATTENTION : les fourchettes TARIFS_VEHICULES plus bas sont des EXEMPLES.
Adaptez-les a vos devis de pieces et main d'oeuvre avant ouverture.
Le formulaire vehicule renseigne une ESTIMATION non contractuelle.
"""

import hmac
import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, Response, abort, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parent
app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024

DB_PATH = os.environ.get("DATABASE_PATH", str(ROOT / "data" / "garage.db"))
PARIS = ZoneInfo("Europe/Paris")

with (ROOT / "services.json").open(encoding="utf-8") as f:
    SERVICES = json.load(f)
CATALOGUE = {s["id"]: s for s in SERVICES}
# Tous les elements du nouveau module sont contenus dans ce seul fichier.
# Les JS/CSS ci-dessous sont servis par Flask, sans avoir a les creer sur GitHub.
ESTIMATION_JS = r'''
/* K2 Motor - estimateur et prise de rendez-vous (gratuit, sans API plaque). */
(() => {
  'use strict';
  if (document.getElementById('k2vp-launcher')) return;
  const $ = (id) => document.getElementById(id);
  const euro = (n) => Number(n).toLocaleString('fr-FR') + ' €';
  let catalog = null;
  let services = [];
  let currentEstimate = null;

  const launcher = document.createElement('button');
  launcher.id = 'k2vp-launcher';
  launcher.type = 'button';
  launcher.textContent = '🚗 Estimer mon tarif';
  launcher.setAttribute('aria-haspopup', 'dialog');
  launcher.setAttribute('aria-controls', 'k2vp-dialog');

  const dialog = document.createElement('div');
  dialog.id = 'k2vp-overlay';
  dialog.hidden = true;
  dialog.innerHTML = `
    <section id="k2vp-dialog" role="dialog" aria-modal="true" aria-label="Estimation de prix K2 Motor">
      <header class="k2vp-header">
        <div><strong>K2 <span>MOTOR</span></strong><small>Devis indicatif personnalisé</small></div>
        <button type="button" id="k2vp-close" aria-label="Fermer">×</button>
      </header>
      <div class="k2vp-content">
        <p class="k2vp-lead">Choisissez votre véhicule et les prestations. Aucun compte et aucune plaque nécessaires.</p>
        <div id="k2vp-error" role="alert" aria-live="polite" hidden></div>
        <form id="k2vp-vehicle-form">
          <h3>1. Votre véhicule</h3>
          <div class="k2vp-grid">
            <label>Marque<select id="k2vp-brand" required><option value="">Choisir une marque</option></select></label>
            <label id="k2vp-custombrand-wrap" hidden>Autre marque<input id="k2vp-custombrand" maxlength="45" placeholder="Ex. Suzuki"></label>
            <label>Modèle<select id="k2vp-model" required><option value="">Choisir un modèle</option></select></label>
            <label id="k2vp-custommodel-wrap" hidden>Autre modèle<input id="k2vp-custommodel" maxlength="60" placeholder="Ex. Swift"></label>
            <label id="k2vp-segment-wrap" hidden>Gabarit du véhicule<select id="k2vp-segment"><option value="">Choisir un gabarit</option></select></label>
            <label>Année<input id="k2vp-year" type="number" min="1990" max="2030" inputmode="numeric" required placeholder="Ex. 2018"></label>
            <label>Motorisation<select id="k2vp-fuel" required><option value="">Choisir</option></select></label>
          </div>
          <p class="k2vp-hint">Le modèle ne détermine pas à lui seul les références des pièces : le devis final dépend de la motorisation exacte et de la carte grise.</p>
          <h3>2. Prestations souhaitées</h3>
          <div id="k2vp-services" class="k2vp-services"><p>Chargement des prestations…</p></div>
          <button class="k2vp-main" id="k2vp-calculate" type="submit">Calculer mon estimation</button>
        </form>
        <section id="k2vp-result" hidden aria-live="polite">
          <h3>3. Votre estimation</h3>
          <div class="k2vp-summary" id="k2vp-summary"></div>
          <div id="k2vp-lines"></div>
          <div class="k2vp-total"><span>Fourchette estimée</span><strong id="k2vp-total"></strong></div>
          <p class="k2vp-notice">Estimation non contractuelle, hors frais de déplacement. Prix final confirmé par le garage après vérification des références, de l’huile adaptée et des pièces.</p>
          <button class="k2vp-main" id="k2vp-book-toggle" type="button">Choisir un rendez-vous</button>
        </section>
        <section id="k2vp-booking" hidden>
          <h3>4. Votre rendez-vous</h3>
          <p class="k2vp-hint">La réservation fixe un créneau, pas le prix définitif. K2 Motor confirmera le devis avant l’intervention.</p>
          <form id="k2vp-book-form">
            <div class="k2vp-grid">
              <label>Date souhaitée<input id="k2vp-date" type="date" required></label>
              <label>Créneau disponible<select id="k2vp-slot" required><option value="">Choisissez d'abord une date</option></select></label>
              <label>Nom et prénom<input id="k2vp-name" type="text" minlength="2" maxlength="150" autocomplete="name" required></label>
              <label>Téléphone<input id="k2vp-phone" type="tel" minlength="8" maxlength="25" autocomplete="tel" required></label>
              <label class="k2vp-full">E-mail<input id="k2vp-email" type="email" maxlength="150" autocomplete="email" required></label>
              <label class="k2vp-full k2vp-consent"><input id="k2vp-consent" type="checkbox" required><span>J'accepte que mes informations soient utilisées pour organiser mon rendez-vous. <a href="#legal" target="_self">Informations de confidentialité</a>.</span></label>
            </div>
            <button id="k2vp-send" class="k2vp-main" type="submit">Réserver ce créneau</button>
          </form>
        </section>
        <div id="k2vp-confirmation" role="status" hidden></div>
      </div>
    </section>`;
  document.body.append(launcher, dialog);

  function error(message) {
    const box = $('k2vp-error');
    box.textContent = message || '';
    box.hidden = !message;
    if (message) box.scrollIntoView({block:'nearest',behavior:'smooth'});
  }
  async function request(url, options) {
    const response = await fetch(url, options);
    let payload = {};
    try { payload = await response.json(); } catch (_) { /* HTML error page */ }
    if (!response.ok) throw new Error(payload.error || 'Le service est momentanément indisponible.');
    return payload;
  }
  function option(select, value, label) {
    const elt = document.createElement('option');
    elt.value = value;
    elt.textContent = label;
    select.append(elt);
  }
  function refreshModels() {
    const brand = $('k2vp-brand').value;
    const models = $('k2vp-model');
    models.replaceChildren();
    option(models, '', 'Choisir un modèle');
    for (const name of Object.keys((catalog?.brands || {})[brand] || {})) option(models, name, name);
    option(models, '__other__', 'Autre modèle / non répertorié');
    $('k2vp-custombrand-wrap').hidden = brand !== '__other__';
    $('k2vp-custommodel-wrap').hidden = true;
    $('k2vp-segment-wrap').hidden = true;
  }
  function refreshCustomModel() {
    const other = $('k2vp-model').value === '__other__';
    $('k2vp-custommodel-wrap').hidden = !other;
    $('k2vp-segment-wrap').hidden = !other;
  }
  function selectedServices() {
    return [...document.querySelectorAll('#k2vp-services input:checked')].map(e => e.value);
  }
  function vehicle() {
    return {
      brand: $('k2vp-brand').value === '__other__' ? $('k2vp-custombrand').value.trim() : $('k2vp-brand').value,
      model: $('k2vp-model').value === '__other__' ? $('k2vp-custommodel').value.trim() : $('k2vp-model').value,
      year: Number($('k2vp-year').value),
      fuel: $('k2vp-fuel').value,
      segment: $('k2vp-segment').value
    };
  }
  function renderQuote(estimate) {
    const v = estimate.vehicle;
    const summary = $('k2vp-summary');
    summary.textContent = `${v.brand} ${v.model} · ${v.year} · ${catalog.fuels[v.fuel]}`;
    const lines = $('k2vp-lines');
    lines.replaceChildren();
    estimate.items.forEach(item => {
      const row = document.createElement('div');
      row.className = 'k2vp-line';
      const title = document.createElement('span');
      title.textContent = item.name;
      const value = document.createElement('strong');
      value.textContent = `${euro(item.min)} – ${euro(item.max)}`;
      row.append(title, value);
      lines.append(row);
    });
    $('k2vp-total').textContent = `${euro(estimate.min)} – ${euro(estimate.max)}`;
    $('k2vp-result').hidden = false;
    $('k2vp-booking').hidden = true;
    $('k2vp-confirmation').hidden = true;
    $('k2vp-result').scrollIntoView({block:'nearest',behavior:'smooth'});
  }
  function invalidateEstimate() {
    currentEstimate = null;
    $('k2vp-result').hidden = true;
    $('k2vp-booking').hidden = true;
    $('k2vp-confirmation').hidden = true;
  }
  function open() {
    dialog.hidden = false;
    document.body.classList.add('k2vp-open');
    $('k2vp-brand').focus();
  }
  function close() {
    dialog.hidden = true;
    document.body.classList.remove('k2vp-open');
    launcher.focus();
  }
  launcher.addEventListener('click', open);
  $('k2vp-close').addEventListener('click', close);
  dialog.addEventListener('click', e => { if (e.target === dialog) close(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !dialog.hidden) close(); });
  $('k2vp-brand').addEventListener('change', () => { refreshModels(); invalidateEstimate(); });
  $('k2vp-model').addEventListener('change', () => { refreshCustomModel(); invalidateEstimate(); });
  $('k2vp-vehicle-form').addEventListener('input', invalidateEstimate);
  $('k2vp-vehicle-form').addEventListener('change', invalidateEstimate);

  $('k2vp-vehicle-form').addEventListener('submit', async e => {
    e.preventDefault();
    error('');
    const ids = selectedServices();
    if (!ids.length) { error('Choisissez au moins une prestation.'); return; }
    if (ids.includes('vidange') && ids.includes('huile')) { error('La vidange comprend déjà le filtre à huile.'); return; }
    if (ids.includes('disques') && ids.includes('plaquettes')) { error('Disques + plaquettes comprend déjà les plaquettes sur le même essieu.'); return; }
    const button = $('k2vp-calculate');
    button.disabled = true;
    try {
      currentEstimate = await request('/api/vehicle/estimate', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({vehicle:vehicle(), services:ids})
      });
      renderQuote(currentEstimate);
    } catch (ex) { error(ex.message); }
    finally { button.disabled = false; }
  });

  $('k2vp-book-toggle').addEventListener('click', () => {
    if (!currentEstimate) return;
    $('k2vp-booking').hidden = false;
    $('k2vp-booking').scrollIntoView({block:'nearest',behavior:'smooth'});
    $('k2vp-date').focus();
  });
  $('k2vp-date').addEventListener('change', async () => {
    const select = $('k2vp-slot');
    select.replaceChildren();
    option(select, '', 'Recherche des créneaux…');
    if (!currentEstimate || !$('k2vp-date').value) return;
    try {
      const params = new URLSearchParams({date:$('k2vp-date').value, services:selectedServices().join(',')});
      const slots = await request('/api/slots?' + params.toString());
      select.replaceChildren();
      option(select, '', slots.length ? 'Choisir une heure' : 'Aucun créneau ce jour');
      slots.forEach(s => option(select, s, s.slice(11)));
    } catch (ex) { error(ex.message); select.replaceChildren(); option(select,'','Indisponible'); }
  });
  $('k2vp-book-form').addEventListener('submit', async e => {
    e.preventDefault();
    error('');
    if (!currentEstimate) { error('Recalculez d’abord votre estimation.'); return; }
    if (!$('k2vp-slot').value) { error('Veuillez sélectionner une heure.'); return; }
    const button = $('k2vp-send');
    button.disabled = true;
    try {
      const output = await request('/api/bookings', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({
          vehicle_profile:vehicle(), vehicle:`${vehicle().brand} ${vehicle().model}`,
          services:selectedServices(), start:$('k2vp-slot').value,
          name:$('k2vp-name').value.trim(), email:$('k2vp-email').value.trim(),
          phone:$('k2vp-phone').value.trim()
        })
      });
      const done = $('k2vp-confirmation');
      done.replaceChildren();
      const headline = document.createElement('h3');
      headline.textContent = 'Créneau réservé !';
      const explanation = document.createElement('p');
      explanation.textContent = 'Référence : ' + output.id + '. Code personnel de réservation : ' + output.token;
      const note = document.createElement('p');
      note.textContent = 'Conservez ce code. K2 Motor devra confirmer les pièces, les frais de déplacement et le devis final avant intervention.';
      const copy = document.createElement('button');
      copy.className = 'k2vp-main'; copy.type = 'button'; copy.textContent = 'Copier les informations';
      copy.addEventListener('click', async () => {
        try { await navigator.clipboard.writeText(explanation.textContent + '\n' + note.textContent); copy.textContent = 'Informations copiées'; }
        catch (_) { copy.textContent = 'Notez le code affiché ci-dessus'; }
      });
      done.append(headline, explanation, note, copy);
      done.hidden = false;
      $('k2vp-booking').hidden = true;
      $('k2vp-result').hidden = true;
      done.scrollIntoView({block:'nearest',behavior:'smooth'});
    } catch (ex) { error(ex.message); }
    finally { button.disabled = false; }
  });

  async function initialize() {
    try {
      const [c, s] = await Promise.all([request('/api/vehicle/catalog'), request('/api/services')]);
      catalog = c;
      services = s;
      const brand = $('k2vp-brand');
      Object.keys(c.brands).sort((a,b)=>a.localeCompare(b,'fr')).forEach(n => option(brand,n,n));
      option(brand,'__other__','Autre marque');
      for (const [key,label] of Object.entries(c.segments)) option($('k2vp-segment'),key,label);
      for (const [key,label] of Object.entries(c.fuels)) option($('k2vp-fuel'),key,label);
      const serviceList = $('k2vp-services');
      serviceList.replaceChildren();
      s.forEach(svc => {
        const label = document.createElement('label');
        const check = document.createElement('input');
        check.type = 'checkbox'; check.value = svc.id;
        const span = document.createElement('span');
        span.textContent = svc.name;
        label.append(check,span);
        serviceList.append(label);
      });
      const now = new Date();
      const today = new Date(now.getTime() - now.getTimezoneOffset()*60000).toISOString().slice(0,10);
      $('k2vp-date').min = today;
      const future = new Date(now.getTime() + 90*86400000);
      $('k2vp-date').max = new Date(future.getTime()-future.getTimezoneOffset()*60000).toISOString().slice(0,10);
      $('k2vp-year').max = String(new Date().getFullYear() + 1);
      $('k2vp-year').value = String(new Date().getFullYear()-8);
    } catch (ex) { error('Chargement impossible : ' + ex.message); }
  }
  initialize();
})();

'''

ESTIMATION_CSS = r'''
/* Styles isolés : n'écrasent pas la mise en page K2 Motor existante. */
#k2vp-launcher{position:fixed;z-index:2147483000;right:18px;bottom:115px;border:0;border-radius:100px;background:#ef303b;color:#fff;padding:15px 23px;font:800 15px/1.2 system-ui,-apple-system,Arial,sans-serif;box-shadow:0 8px 28px rgba(0,0,0,.4);cursor:pointer;}
#k2vp-launcher:hover{background:#ff4c56;transform:translateY(-2px)}
#k2vp-launcher:focus-visible,#k2vp-overlay button:focus-visible,#k2vp-overlay select:focus-visible,#k2vp-overlay input:focus-visible{outline:3px solid #ff4c56;outline-offset:2px}
body.k2vp-open{overflow:hidden!important}
#k2vp-overlay[hidden],#k2vp-overlay [hidden]{display:none!important}
#k2vp-overlay{position:fixed;inset:0;z-index:2147483001;background:rgba(0,0,0,.80);padding:12px;display:grid;place-items:center;font:15px/1.45 system-ui,-apple-system,Arial,sans-serif;color:#f4f4f4}
#k2vp-overlay,#k2vp-overlay *{box-sizing:border-box}
#k2vp-dialog{width:min(100%,620px);max-height:94dvh;display:flex;flex-direction:column;border:1px solid #3e4148;border-radius:22px;overflow:hidden;background:#17191d;box-shadow:0 22px 70px rgba(0,0,0,.55)}
#k2vp-overlay .k2vp-header{display:flex;align-items:center;justify-content:space-between;padding:16px 20px;background:#202329;border-bottom:1px solid #353840;flex-shrink:0}
#k2vp-overlay .k2vp-header strong{font-size:24px;letter-spacing:1px}
#k2vp-overlay .k2vp-header strong span{color:#ef303b}
#k2vp-overlay .k2vp-header small{display:block;font-size:12px;color:#aaa;letter-spacing:0}
#k2vp-close{border:0;color:#fff;background:transparent;font:30px/1 Arial,sans-serif;cursor:pointer;min-width:42px;min-height:42px}
#k2vp-overlay .k2vp-content{padding:18px 20px 28px;overflow-y:auto;overscroll-behavior:contain}
#k2vp-overlay .k2vp-lead{font-size:15px;color:#d9dde4;margin:0 0 14px}
#k2vp-overlay h3{font-size:17px;color:#fff;margin:20px 0 12px;font-weight:780}
#k2vp-overlay .k2vp-grid{display:grid;grid-template-columns:1fr 1fr;gap:13px}
#k2vp-overlay label{color:#c7cad0;font-weight:650;display:flex;flex-direction:column;gap:5px;font-size:13px}
#k2vp-overlay input,#k2vp-overlay select{background:#272b32;border:1px solid #4f535c;border-radius:9px;color:#fff;min-height:43px;padding:9px 10px;font:500 14px system-ui,-apple-system,Arial,sans-serif;max-width:100%;width:100%;color-scheme:dark}
#k2vp-overlay input::placeholder{color:#b3b5bc}
#k2vp-overlay .k2vp-full{grid-column:1/-1}
#k2vp-overlay .k2vp-hint{color:#aab0ba;font-size:12px;margin:12px 0;line-height:1.45}
#k2vp-overlay .k2vp-services{display:grid;grid-template-columns:1fr 1fr;gap:7px}
#k2vp-overlay .k2vp-services label{display:flex;flex-direction:row;align-items:center;gap:8px;padding:10px 8px;border:1px solid #444850;background:#25272d;border-radius:9px;font-size:13px;cursor:pointer;line-height:1.2}
#k2vp-overlay .k2vp-services input{appearance:auto;accent-color:#ef303b;width:17px;height:17px;min-height:auto;flex:none}
#k2vp-overlay .k2vp-main{display:block;width:100%;min-height:44px;padding:12px 16px;margin:16px 0 8px;background:#ef303b;color:#fff;border:0;border-radius:11px;font-size:15px;font-weight:850;cursor:pointer}
#k2vp-overlay .k2vp-main:disabled{opacity:.55;cursor:wait}
#k2vp-overlay .k2vp-summary{color:#c7ced9;font-size:13px;margin-bottom:10px}
#k2vp-overlay .k2vp-line{display:flex;justify-content:space-between;gap:15px;padding:9px 0;border-bottom:1px solid #353943}
#k2vp-overlay .k2vp-line strong{white-space:nowrap}
#k2vp-overlay .k2vp-total{display:flex;align-items:center;justify-content:space-between;gap:10px;background:#2b2024;border:1px solid #693940;border-radius:11px;padding:14px;margin-top:13px}
#k2vp-overlay .k2vp-total strong{font-size:21px;color:#ff4c56}
#k2vp-overlay .k2vp-notice{color:#c9c3b8;font-size:12px;line-height:1.55}
#k2vp-error{background:#48272a;border:1px solid #a64b52;color:#fff;padding:12px;margin:10px 0;border-radius:8px}
#k2vp-confirmation{padding:14px;background:#20392a;border:1px solid #366848;border-radius:12px;overflow-wrap:anywhere}
#k2vp-overlay #k2vp-confirmation p{font-size:14px}
@media(max-width:500px){#k2vp-overlay{padding:0}#k2vp-dialog{width:100%;max-height:100dvh;height:100dvh;border-radius:0}#k2vp-overlay .k2vp-grid,#k2vp-overlay .k2vp-services{grid-template-columns:1fr}#k2vp-overlay .k2vp-content{padding:14px 16px}#k2vp-launcher{right:12px;bottom:112px;padding:14px 18px}}

#k2vp-overlay .k2vp-consent{display:flex;flex-direction:row;align-items:start;gap:10px;margin-top:8px;font-weight:500;}
#k2vp-overlay .k2vp-consent input{width:18px;min-height:18px;height:18px;flex:none;margin:2px 0 0;}
#k2vp-overlay .k2vp-consent a{color:#ff7780;text-decoration:underline;}

'''

# Grille indicative, A PERSONNALISER. Les noms de prestation correspondent
# aux identifiants du fichier services.json existant.
TARIFS_VEHICULES = {'information': "TARIFS PROVISOIRES K2 Motor, non issus d'Auto Pièces 41. Fourchettes indicatives pièces + main-d'oeuvre, sans déplacement. À adapter après réception des vrais prix d'achat. Les estimations par gabarit ne vérifient pas la compatibilité des pièces.", 'supplement_gabarit': {'citadine': 0, 'compacte': 10, 'berline': 20, 'suv': 25, 'monospace': 20, 'utilitaire': 35}, 'supplement_vidange_motorisation': {'essence': 0, 'diesel': 10, 'hybride': 20, 'gpl': 10}, 'prestations': {'vidange': {'min': 129, 'max': 169, 'commentaire': 'Huile et filtre estimés. Norme et quantité à confirmer'}, 'huile': {'min': 49, 'max': 75, 'commentaire': "Filtre compris dans l'estimation"}, 'air': {'min': 49, 'max': 89, 'commentaire': "Filtre compris dans l'estimation"}, 'habitacle': {'min': 55, 'max': 95, 'commentaire': "Filtre compris dans l'estimation"}, 'gasoil': {'min': 79, 'max': 149, 'commentaire': "Filtre compris dans l'estimation"}, 'plaquettes': {'min': 139, 'max': 259, 'commentaire': 'Plaquettes estimées sur un essieu'}, 'disques': {'min': 249, 'max': 499, 'commentaire': 'Disques + plaquettes estimés sur un essieu'}, 'liquide': {'min': 79, 'max': 109, 'commentaire': "Liquide compris dans l'estimation"}, 'diagnostic': {'min': 59, 'max': 89, 'commentaire': 'Lecture et premiers contrôles; réparations exclues'}, 'pneus': {'min': 99, 'max': 139, 'commentaire': '4 pneus hors fournitures, sous réserve de matériel adapté'}, 'batterie': {'min': 149, 'max': 339, 'commentaire': 'Batterie standard estimée; codage éventuel sur devis'}, 'essuie': {'min': 40, 'max': 90, 'commentaire': "Balais courants compris dans l'estimation"}}}

# Compatible avec les projets dont les fichiers HTML/CSS/JS sont à la racine
# et ceux qui les rangent dans le sous-dossier public/.
SITE_ROOT = ROOT if (ROOT / "index.html").is_file() else ROOT / "public"


class Database:
    def __init__(self, connection, postgres):
        self.connection = connection
        self.postgres = postgres

    def execute(self, sql, params=()):
        if self.postgres:
            sql = sql.replace("?", "%s")
        return self.connection.execute(sql, params)

    def lock_bookings(self):
        if self.postgres:
            self.connection.execute("SELECT pg_advisory_xact_lock(2026102901)")
        else:
            self.connection.execute("BEGIN IMMEDIATE")


@contextmanager
def database():
    url = os.environ.get("DATABASE_URL", "").strip()
    if os.environ.get("RENDER", "").lower() == "true" and not url:
        raise RuntimeError("DATABASE_URL est absente de Render > Environment")

    if url:
        import psycopg
        from psycopg.rows import dict_row
        connection = psycopg.connect(url, row_factory=dict_row, connect_timeout=15)
    else:
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(DB_PATH, timeout=15)
        connection.row_factory = sqlite3.Row

    db = Database(connection, postgres=bool(url))
    try:
        if url:
            connection.execute("SELECT pg_advisory_xact_lock(2026102900)")
        db.execute(
            "CREATE TABLE IF NOT EXISTS bookings ("
            "id TEXT PRIMARY KEY, token TEXT UNIQUE, "
            'start TEXT, "end" TEXT, '
            "name TEXT, email TEXT, phone TEXT, vehicle TEXT, "
            "services TEXT, price INTEGER, "
            "status TEXT DEFAULT 'confirmed')"
        )
        connection.commit()
        yield db
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        secret = os.environ.get("ADMIN_TOKEN", "")
        header = request.headers.get("Authorization", "")
        given = header[7:] if header.startswith("Bearer ") else ""
        if len(secret) < 24 or not hmac.compare_digest(secret, given):
            abort(401, "Acces garage refuse.")
        return fn(*args, **kwargs)
    return wrapper


def json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, "Formulaire invalide.")
    return data


def chosen_services(ids):
    if (
        not isinstance(ids, list)
        or not ids
        or any(not isinstance(i, str) or i not in CATALOGUE for i in ids)
        or len(ids) != len(set(ids))
    ):
        abort(400, "Selection de prestations invalide.")
    return [CATALOGUE[i] for i in ids]


def checked_slot(value, minutes):
    try:
        start = datetime.strptime(value, "%Y-%m-%dT%H:%M").replace(tzinfo=PARIS)
    except (ValueError, TypeError):
        abort(400, "Date invalide.")

    end = start + timedelta(minutes=minutes)
    now = datetime.now(PARIS)
    if (
        start <= now + timedelta(hours=1)
        or start > now + timedelta(days=90)
        or start.weekday() == 6
        or start.minute not in (0, 30)
        or start.hour < 9
        or end.date() != start.date()
        or end.hour > 18
        or (end.hour == 18 and end.minute > 0)
    ):
        abort(400, "Creneau indisponible (lundi-samedi, 9h-18h).")

    return start.strftime("%Y-%m-%dT%H:%M"), end.strftime("%Y-%m-%dT%H:%M")


def occupied(db, start, end):
    return db.execute(
        "SELECT 1 FROM bookings WHERE status='confirmed' "
        'AND start < ? AND "end" > ?',
        (end, start),
    ).fetchone() is not None


@app.after_request
def secure_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; "
        "style-src 'self'; script-src 'self'; frame-ancestors 'none'; "
        "base-uri 'self'; form-action 'self'"
    )
    if request.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.errorhandler(400)
@app.errorhandler(401)
@app.errorhandler(404)
@app.errorhandler(409)
@app.errorhandler(413)
def http_error(error):
    return jsonify(error=error.description), error.code


@app.errorhandler(500)
def server_error(error):
    return jsonify(error="Le serveur est momentanement indisponible. Reessayez plus tard."), 500


# Pages et fichiers du site : racine GitHub ou dossier public/.
@app.get("/")
def home():
    # Charge le site existant et ajoute le module sans changer index.html.
    html = (SITE_ROOT / "index.html").read_text(encoding="utf-8")
    css = '<link rel="stylesheet" href="/k2-estimation.css">'
    js = '<script src="/k2-estimation.js" defer></script>'
    if re.search(r"</head\s*>", html, flags=re.IGNORECASE):
        html = re.sub(r"</head\s*>", lambda m: css + "\n" + m.group(), html, count=1, flags=re.IGNORECASE)
    else:
        html = css + html
    if re.search(r"</body\s*>", html, flags=re.IGNORECASE):
        html = re.sub(r"</body\s*>", lambda m: js + "\n" + m.group(), html, count=1, flags=re.IGNORECASE)
    else:
        html += js
    return Response(html, mimetype="text/html")


@app.get("/k2-estimation.js")
def vehicle_script():
    return Response(ESTIMATION_JS, content_type="application/javascript; charset=utf-8")


@app.get("/k2-estimation.css")
def vehicle_styles():
    return Response(ESTIMATION_CSS, content_type="text/css; charset=utf-8")


@app.get("/styles.css")
def stylesheet():
    return send_from_directory(SITE_ROOT, "styles.css")


@app.get("/app.js")
def javascript():
    return send_from_directory(SITE_ROOT, "app.js")


@app.get("/assets/garage-hero.jpg")
@app.get("/garage-hero.jpg")
def hero_image():
    for directory in (SITE_ROOT / "assets", SITE_ROOT, ROOT / "assets", ROOT):
        if (directory / "garage-hero.jpg").is_file():
            return send_from_directory(directory, "garage-hero.jpg")
    abort(404, "Image d'accueil introuvable.")


# Controle Render : ne depend pas d'une connexion a Neon.
@app.get("/health")
def health():
    return jsonify(ok=True)


# --- Estimateur gratuit de prix par véhicule ---
# Les prix sont des repères indicatifs éditables dans TARIFS_VEHICULES (plus haut).
VEHICLE_BRANDS = {
    "Citroën": {"C1": "citadine", "C2": "citadine", "C3": "citadine", "C3 Aircross": "suv", "C4": "compacte", "C4 Picasso": "monospace", "Berlingo": "utilitaire", "Jumpy": "utilitaire"},
    "Peugeot": {"107": "citadine", "108": "citadine", "208": "citadine", "207": "citadine", "308": "compacte", "307": "compacte", "2008": "suv", "3008": "suv", "5008": "suv", "Partner": "utilitaire", "Expert": "utilitaire"},
    "Renault": {"Twingo": "citadine", "Clio": "citadine", "Captur": "suv", "Mégane": "compacte", "Scénic": "monospace", "Kadjar": "suv", "Austral": "suv", "Kangoo": "utilitaire", "Trafic": "utilitaire"},
    "Dacia": {"Sandero": "citadine", "Logan": "compacte", "Duster": "suv", "Jogger": "monospace", "Spring": "citadine"},
    "Volkswagen": {"Polo": "citadine", "Golf": "compacte", "Passat": "berline", "T-Roc": "suv", "Tiguan": "suv", "Caddy": "utilitaire"},
    "Ford": {"Fiesta": "citadine", "Focus": "compacte", "Puma": "suv", "Kuga": "suv", "Transit": "utilitaire"},
    "Opel": {"Corsa": "citadine", "Astra": "compacte", "Mokka": "suv", "Crossland": "suv", "Combo": "utilitaire"},
    "Toyota": {"Aygo": "citadine", "Yaris": "citadine", "Corolla": "compacte", "C-HR": "suv", "RAV4": "suv", "Proace": "utilitaire"},
    "Fiat": {"500": "citadine", "Panda": "citadine", "Tipo": "compacte", "Doblo": "utilitaire", "Ducato": "utilitaire"},
    "Nissan": {"Micra": "citadine", "Juke": "suv", "Qashqai": "suv", "X-Trail": "suv"},
    "Hyundai": {"i10": "citadine", "i20": "citadine", "i30": "compacte", "Kona": "suv", "Tucson": "suv"},
    "Kia": {"Picanto": "citadine", "Rio": "citadine", "Ceed": "compacte", "Stonic": "suv", "Sportage": "suv"},
    "BMW": {"Série 1": "compacte", "Série 3": "berline", "X1": "suv", "X3": "suv"},
    "Mercedes-Benz": {"Classe A": "compacte", "Classe C": "berline", "GLA": "suv", "Vito": "utilitaire"},
    "Audi": {"A1": "citadine", "A3": "compacte", "A4": "berline", "Q2": "suv", "Q3": "suv"},
    "Seat": {"Ibiza": "citadine", "Leon": "compacte", "Arona": "suv", "Ateca": "suv"},
    "Skoda": {"Fabia": "citadine", "Octavia": "berline", "Kamiq": "suv", "Karoq": "suv"},
    "Volvo": {"V40": "compacte", "V60": "berline", "XC40": "suv", "XC60": "suv"},
}
FUEL_LABELS = {"essence": "Essence", "diesel": "Diesel", "hybride": "Hybride", "gpl": "GPL", "electrique": "Électrique"}
SEGMENTS = {"citadine": "Citadine", "compacte": "Compacte", "berline": "Berline", "suv": "SUV", "monospace": "Monospace", "utilitaire": "Utilitaire"}


def vehicle_data(raw):
    if not isinstance(raw, dict):
        abort(400, "Choix du véhicule manquant.")
    brand, model = raw.get("brand"), raw.get("model")
    fuel, year = raw.get("fuel"), raw.get("year")
    if not all(isinstance(v, str) for v in (brand, model, fuel)):
        abort(400, "Marque, modèle ou motorisation invalide.")
    brand, model = brand.strip(), model.strip()
    if not 2 <= len(brand) <= 45 or not 1 <= len(model) <= 60:
        abort(400, "Marque ou modèle invalide.")
    if not re.fullmatch(r"[\wÀ-ÿ .\-/+]+", brand + " " + model, flags=re.UNICODE):
        abort(400, "Caractères non autorisés dans le véhicule.")
    if fuel not in FUEL_LABELS:
        abort(400, "Motorisation inconnue.")
    try:
        year = int(year)
    except (TypeError, ValueError):
        abort(400, "Année du véhicule invalide.")
    if not 1990 <= year <= datetime.now(PARIS).year + 1:
        abort(400, "Année du véhicule hors limites.")
    known = VEHICLE_BRANDS.get(brand, {}).get(model)
    if known:
        segment = known
    else:
        segment = raw.get("segment")
        if segment not in SEGMENTS:
            abort(400, "Choisissez le gabarit du véhicule.")
    return {"brand": brand, "model": model, "year": year, "fuel": fuel, "segment": segment}


def vehicle_estimate(profile, selected):
    vehicle = vehicle_data(profile)
    chosen_ids = {service["id"] for service in selected}
    if ("vidange" in chosen_ids and "huile" in chosen_ids) or (
        "disques" in chosen_ids and "plaquettes" in chosen_ids
    ):
        abort(400, "Prestations doublées : un forfait inclut déjà l'autre pièce.")
    rates = TARIFS_VEHICULES
    surcharge = rates["supplement_gabarit"][vehicle["segment"]]
    items = []
    for service in selected:
        sid = service["id"]
        if vehicle["fuel"] == "electrique" and sid in ("vidange", "huile", "gasoil"):
            abort(400, "Cette prestation n'est pas adaptée à un véhicule électrique.")
        if vehicle["fuel"] != "diesel" and sid == "gasoil":
            abort(400, "Le filtre à gasoil concerne les véhicules diesel.")
        setting = rates["prestations"].get(sid)
        if not setting:
            abort(400, "Estimation indisponible pour une prestation.")
        low, high = setting["min"], setting["max"]
        if sid == "vidange":
            extra = rates["supplement_vidange_motorisation"].get(vehicle["fuel"], 0)
            low += extra
            high += extra
        if sid in ("vidange", "huile", "air", "habitacle", "gasoil", "plaquettes", "disques", "batterie"):
            low += surcharge
            high += surcharge
        items.append({"id": sid, "name": service["name"], "min": low, "max": high})
    return {
        "vehicle": vehicle, "items": items,
        "min": sum(x["min"] for x in items), "max": sum(x["max"] for x in items),
        "message": "Estimation non contractuelle : pièces, huile conforme à la norme constructeur, frais de déplacement et devis définitif à confirmer avant intervention."
    }


@app.get("/api/vehicle/catalog")
def vehicle_catalog():
    return jsonify(brands=VEHICLE_BRANDS, segments=SEGMENTS, fuels=FUEL_LABELS)


@app.post("/api/vehicle/estimate")
def estimate_vehicle():
    data = json_body()
    selected = chosen_services(data.get("services"))
    return jsonify(vehicle_estimate(data.get("vehicle"), selected))


@app.get("/api/services")
def services():
    return jsonify(SERVICES)


@app.get("/api/slots")
def available_slots():
    selected = chosen_services(request.args.get("services", "").split(","))
    duration = sum(s["minutes"] for s in selected)
    day = request.args.get("date", "")
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        abort(400, "Date invalide.")

    result = []
    with database() as db:
        existing = db.execute(
            "SELECT start, \"end\" FROM bookings WHERE status='confirmed' "
            'AND start < ? AND "end" > ?',
            (day + "T23:59", day + "T00:00"),
        ).fetchall()
        for minute in range(9 * 60, 18 * 60, 30):
            candidate = f"{day}T{minute // 60:02d}:{minute % 60:02d}"
            try:
                start, end = checked_slot(candidate, duration)
            except Exception as error:
                if getattr(error, "code", None) == 400:
                    continue
                raise
            if not any(row["start"] < end and row["end"] > start for row in existing):
                result.append(candidate)
    return jsonify(result)


@app.post("/api/bookings")
def create_booking():
    data = json_body()
    selected = chosen_services(data.get("services"))
    for field in ("name", "email", "phone"):
        value = data.get(field)
        if not isinstance(value, str) or not 2 <= len(value.strip()) <= 150:
            abort(400, "Coordonnees incompletes ou trop longues.")
        data[field] = value.strip()

    if (
        not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", data["email"])
        or not re.fullmatch(r"[+\d ().-]{8,25}", data["phone"])
    ):
        abort(400, "Email ou telephone invalide.")

    # Les anciennes réservations restent compatibles avec app.js d'origine.
    # Le module véhicule, lui, enregistre un prix « à partir de » recalculé
    # côté serveur (jamais un prix dicté par le navigateur).
    if "vehicle_profile" in data:
        quote = vehicle_estimate(data["vehicle_profile"], selected)
        vehicle = quote["vehicle"]
        vehicle_text = (
            f'{vehicle["brand"]} {vehicle["model"]} '
            f'({vehicle["year"]}, {FUEL_LABELS[vehicle["fuel"]]})'
        )
        booking_price = quote["min"]
    else:
        vehicle_text = data.get("vehicle")
        if not isinstance(vehicle_text, str) or not 2 <= len(vehicle_text.strip()) <= 150:
            abort(400, "Véhicule manquant ou trop long.")
        vehicle_text = vehicle_text.strip()
        booking_price = sum(s["price"] for s in selected)

    start, end = checked_slot(
        data.get("start"), sum(s["minutes"] for s in selected)
    )
    ident = secrets.token_hex(5).upper()
    token = secrets.token_urlsafe(32)
    with database() as db:
        db.lock_bookings()
        if occupied(db, start, end):
            abort(409, "Ce creneau vient d'etre reserve. Choisissez-en un autre.")
        db.execute(
            "INSERT INTO bookings "
            '(id, token, start, "end", name, email, phone, vehicle, services, price) '
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ident, token, start, end, data["name"], data["email"],
                data["phone"], vehicle_text,
                json.dumps([s["id"] for s in selected]),
                booking_price,
            ),
        )
    result = {"id": ident, "token": token}
    if "vehicle_profile" in data:
        result["estimate"] = quote
    return jsonify(result), 201


@app.post("/api/lookup")
def lookup_booking():
    token = json_body().get("token", "")
    if not isinstance(token, str):
        abort(400, "Code invalide.")
    with database() as db:
        row = db.execute(
            "SELECT * FROM bookings WHERE token=?", (token,)
        ).fetchone()
    if row is None:
        abort(404, "Reservation introuvable.")
    result = dict(row)
    result.pop("token", None)
    return jsonify(result)


@app.post("/api/cancel")
def cancel_booking():
    token = json_body().get("token", "")
    if not isinstance(token, str):
        abort(400, "Code invalide.")
    with database() as db:
        row = db.execute(
            "SELECT * FROM bookings WHERE token=?", (token,)
        ).fetchone()
        if row is None:
            abort(404, "Reservation introuvable.")
        if row["status"] != "confirmed":
            abort(409, "Ce rendez-vous n'est plus actif.")
        if row["start"] <= datetime.now(PARIS).strftime("%Y-%m-%dT%H:%M"):
            abort(400, "Ce rendez-vous est deja passe.")
        db.execute(
            "UPDATE bookings SET status='cancelled' WHERE token=?",
            (token,),
        )
    return jsonify(ok=True)


@app.get("/api/admin/bookings")
@require_admin
def admin_bookings():
    with database() as db:
        rows = db.execute(
            'SELECT id, start, "end", name, email, phone, vehicle, '
            "services, price, status FROM bookings ORDER BY start DESC"
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/api/admin/status")
@require_admin
def admin_status():
    data = json_body()
    if (
        data.get("status") not in ("cancelled", "completed")
        or not isinstance(data.get("id"), str)
    ):
        abort(400, "Demande invalide.")
    with database() as db:
        updated = db.execute(
            "UPDATE bookings SET status=? WHERE id=?",
            (data["status"], data["id"]),
        )
        if updated.rowcount == 0:
            abort(404, "Rendez-vous introuvable.")
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))
