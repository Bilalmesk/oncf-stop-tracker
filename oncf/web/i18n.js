/* Small, dependency-free UI translations. English is the fallback. */
const I18N = {
  en: {
    "nav.plan": "Plan journey",
    "nav.planShort": "Plan",
    "nav.live": "Live",
    "nav.map": "Map",
    "status.title": "Where is it now?",
    "status.estimate": "Timetable estimate",
    "status.refresh": "Refresh",
    "status.report": "Report a problem",
    "status.onboard": "I'm on this trip",
    "status.leave": "Leave trip",
    "status.prompt": "You're {distance} from {station}. Are you on this trip?",
    "status.yes": "Yes, I'm on board",
    "status.no": "Not now",
    "status.note": "This is a timetable estimate, not GPS. ONCF publishes no train positions.",
    "status.notDeparted": "Not departed — {station}",
    "status.at": "At {station}",
    "status.running": "En route: {from} → {to}",
    "status.arrived": "Arrived at {station}",
    "status.unknown": "Trip status unavailable",
    "status.liveBoard": "live board",
    "status.communityReport": "community report",
    "status.estimateSource": "estimate",
    "status.liveOnTime": "Live board: on time",
    "status.noDelay": "No reported delay",
    "reports.unverified": "Community reports are unverified.",
    "reports.empty": "No community reports for this trip yet.",
    "reports.confirm": "Confirm",
    "reports.confirmed": "Confirmed",
    "reports.dispute": "Dispute",
    "reports.disputed": "Disputed",
    "reports.score": "{confirm} confirm · {dispute} dispute",
    "report.title": "Report a problem",
    "report.intro": "Tell other travellers what you see at the station. This is a community report, not an official ONCF message.",
    "report.kind": "What happened?",
    "report.delay": "Departure delay",
    "report.cancellation": "Cancellation",
    "report.other": "Other",
    "report.train": "Train",
    "report.station": "Station",
    "report.minutes": "Delay (minutes)",
    "report.message": "Message (optional)",
    "report.placeholder": "e.g. boarding has not started, platform change…",
    "report.cancel": "Cancel",
    "report.submit": "Send report",
    "dest.title": "Direct destinations",
    "dest.from": "from {station}",
    "dest.note": "{count} direct destination(s) · no changes",
    "dest.rail": "rail",
    "dest.coach": "coach",
    "dest.empty": "No direct destinations for this date.",
    "punct.title": "Observed punctuality",
    "punct.local": "local",
    "punct.empty": "No local observations yet. Open a live departure board.",
    "punct.headline": "{n} observations · {pct}% on time (±5 min) · average +{avg} min",
    "punct.detail": "{delayed} delayed · {severe} severe (>15 min) · {stations} stations · {trains} trains",
    "punct.note": "Local observations from live station boards; not an official ONCF punctuality dataset.",
    "advisory.cancellation": "Community reports this train as cancelled. This is not an official ONCF cancellation.",
    "advisory.delay": "Departure delay reported by the community; unverified.",
    "source.odbl": "Timetable data from oncf-gtfs-unofficial (ODbL), built from ONCF’s published timetables; station locations © OpenStreetMap contributors.",
    "lang.label": "Language"
  },
  fr: {
    "nav.plan": "Planifier",
    "nav.planShort": "Planifier",
    "nav.live": "Direct",
    "nav.map": "Carte",
    "status.title": "Où est-il maintenant ?",
    "status.estimate": "Estimation horaire",
    "status.refresh": "Actualiser",
    "status.report": "Signaler un problème",
    "status.onboard": "Je suis dans ce train",
    "status.leave": "Quitter le trajet",
    "status.prompt": "Vous êtes à {distance} de {station}. Êtes-vous dans ce train ?",
    "status.yes": "Oui, je suis à bord",
    "status.no": "Pas maintenant",
    "status.note": "Estimation à partir de l'horaire, pas du GPS. L'ONCF ne publie pas la position des trains.",
    "status.notDeparted": "Pas encore parti — {station}",
    "status.at": "À {station}",
    "status.running": "En route : {from} → {to}",
    "status.arrived": "Arrivé à {station}",
    "status.unknown": "Statut indisponible",
    "status.liveBoard": "tableau en direct",
    "status.communityReport": "signalement communautaire",
    "status.estimateSource": "estimation",
    "status.liveOnTime": "Tableau en direct : à l'heure",
    "status.noDelay": "Aucun retard signalé",
    "reports.unverified": "Les signalements de la communauté ne sont pas vérifiés.",
    "reports.empty": "Aucun signalement pour ce trajet.",
    "reports.confirm": "Confirmer",
    "reports.confirmed": "Confirmé",
    "reports.dispute": "Contester",
    "reports.disputed": "Contesté",
    "reports.score": "{confirm} confirmation · {dispute} contestation",
    "report.title": "Signaler un problème",
    "report.intro": "Dites aux autres voyageurs ce que vous voyez en gare. Ceci est un signalement communautaire, pas un message officiel de l'ONCF.",
    "report.kind": "Que s'est-il passé ?",
    "report.delay": "Retard au départ",
    "report.cancellation": "Annulation",
    "report.other": "Autre",
    "report.train": "Train",
    "report.station": "Gare",
    "report.minutes": "Retard (minutes)",
    "report.message": "Message (facultatif)",
    "report.placeholder": "ex. embarquement non commencé, changement de quai…",
    "report.cancel": "Annuler",
    "report.submit": "Envoyer le signalement",
    "dest.title": "Destinations directes",
    "dest.from": "depuis {station}",
    "dest.note": "{count} destination(s) directe(s) · sans changement",
    "dest.rail": "rail",
    "dest.coach": "car",
    "dest.empty": "Aucune destination directe pour cette date.",
    "punct.title": "Ponctualité observée",
    "punct.local": "local",
    "punct.empty": "Aucune observation locale pour le moment. Ouvrez un tableau de départ.",
    "punct.headline": "{n} observations · {pct}% à l'heure (±5 min) · retard moyen +{avg} min",
    "punct.detail": "{delayed} en retard · {severe} sévère (>15 min) · {stations} gares · {trains} trains",
    "punct.note": "Observations locales issues des tableaux de départ ; pas une statistique officielle ONCF.",
    "advisory.cancellation": "La communauté signale ce train comme annulé. Ce n'est pas une annulation officielle de l'ONCF.",
    "advisory.delay": "Retard au départ signalé par la communauté ; non vérifié.",
    "source.odbl": "Données horaires de oncf-gtfs-unofficial (ODbL), issues des horaires publiés par l'ONCF ; localisation des gares © contributeurs OpenStreetMap.",
    "lang.label": "Langue"
  },
  ar: {
    "nav.plan": "تخطيط الرحلة",
    "nav.planShort": "تخطيط",
    "nav.live": "مباشر",
    "nav.map": "الخريطة",
    "status.title": "أين هو الآن؟",
    "status.estimate": "تقدير حسب الجدول",
    "status.refresh": "تحديث",
    "status.report": "الإبلاغ عن مشكلة",
    "status.onboard": "أنا في هذه الرحلة",
    "status.leave": "مغادرة الرحلة",
    "status.prompt": "أنت على بعد {distance} من {station}. هل أنت في هذه الرحلة؟",
    "status.yes": "نعم، أنا على متن القطار",
    "status.no": "ليس الآن",
    "status.note": "هذا تقدير من الجدول الزمني، وليس GPS. ONCF لا تنشر مواقع القطارات.",
    "status.notDeparted": "لم ينطلق بعد — {station}",
    "status.at": "في {station}",
    "status.running": "في الطريق: {from} ← {to}",
    "status.arrived": "وصل إلى {station}",
    "status.unknown": "حالة الرحلة غير متاحة",
    "status.liveBoard": "لوحة مباشرة",
    "status.communityReport": "بلاغ المجتمع",
    "status.estimateSource": "تقدير",
    "status.liveOnTime": "اللوحة المباشرة: في الموعد",
    "status.noDelay": "لا يوجد تأخير مُبلغ عنه",
    "reports.unverified": "بلاغات المجتمع غير موثوقة.",
    "reports.empty": "لا توجد بلاغات لهذه الرحلة بعد.",
    "reports.confirm": "تأكيد",
    "reports.confirmed": "تم التأكيد",
    "reports.dispute": "اعتراض",
    "reports.disputed": "تم الاعتراض",
    "reports.score": "{confirm} تأكيد · {dispute} اعتراض",
    "report.title": "الإبلاغ عن مشكلة",
    "report.intro": "أخبر المسافرين الآخرين بما تراه في المحطة. هذا بلاغ من المجتمع وليس رسالة رسمية من ONCF.",
    "report.kind": "ما الذي حدث؟",
    "report.delay": "تأخير في الانطلاق",
    "report.cancellation": "إلغاء",
    "report.other": "أخرى",
    "report.train": "القطار",
    "report.station": "المحطة",
    "report.minutes": "التأخير (بالدقائق)",
    "report.message": "رسالة (اختياري)",
    "report.placeholder": "مثال: لم يبدأ الصعود، تغيير الرصيف…",
    "report.cancel": "رجوع",
    "report.submit": "إرسال البلاغ",
    "dest.title": "المحطات المباشرة",
    "dest.from": "من {station}",
    "dest.note": "{count} وجهة مباشرة · بدون تغيير",
    "dest.rail": "سكة حديد",
    "dest.coach": "حافلة",
    "dest.empty": "لا توجد وجهات مباشرة لهذا التاريخ.",
    "punct.title": "الانتظام الملحوظ",
    "punct.local": "محلي",
    "punct.empty": "لا توجد ملاحظات محلية بعد. افتح لوحة المغادرة المباشرة.",
    "punct.headline": "{n} ملاحظة · {pct}% في الموعد (±5 دقائق) · متوسط +{avg} دقيقة",
    "punct.detail": "{delayed} متأخر · {severe} شديد (>15 دقيقة) · {stations} محطات · {trains} قطارات",
    "punct.note": "ملاحظات محلية من لوحات المغادرة؛ ليست إحصاءً رسميًا لانتظام ONCF.",
    "advisory.cancellation": "يبلغ المجتمع عن إلغاء هذا القطار. هذا ليس إلغاءً رسميًا من ONCF.",
    "advisory.delay": "تأخير في الانطلاق حسب بلاغ المجتمع؛ غير موثوق.",
    "source.odbl": "بيانات الجدول من oncf-gtfs-unofficial (ODbL)، مبنية على جداول ONCF المنشورة؛ مواقع المحطات © مساهمو OpenStreetMap.",
    "lang.label": "اللغة"
  }
};

function t(key, vars = {}) {
  const lang = localStorage.getItem("oncf:lang") || "en";
  const table = I18N[lang] || I18N.en;
  let text = table[key] ?? I18N.en[key] ?? key;
  for (const [name, value] of Object.entries(vars)) {
    text = text.replaceAll(`{${name}}`, String(value));
  }
  return text;
}

function currentLang() {
  const saved = localStorage.getItem("oncf:lang") || "en";
  return I18N[saved] ? saved : "en";
}

function applyLanguage(lang) {
  const chosen = I18N[lang] ? lang : "en";
  localStorage.setItem("oncf:lang", chosen);
  document.documentElement.lang = chosen;
  document.documentElement.dir = chosen === "ar" ? "rtl" : "ltr";
  for (const el of document.querySelectorAll("[data-i18n]")) {
    el.textContent = t(el.dataset.i18n);
  }
  for (const el of document.querySelectorAll("[data-i18n-placeholder]")) {
    el.setAttribute("placeholder", t(el.dataset.i18nPlaceholder));
  }
  for (const el of document.querySelectorAll("[data-i18n-title]")) {
    el.setAttribute("title", t(el.dataset.i18nTitle));
  }
  for (const el of document.querySelectorAll("[data-i18n-aria-label]")) {
    el.setAttribute("aria-label", t(el.dataset.i18nAriaLabel));
  }
  if (typeof updateOnboardUi === "function") updateOnboardUi();
  if (typeof renderTripStatus === "function" && typeof state !== "undefined" && state.tripStatus) {
    renderTripStatus(state.tripStatus);
  }
  if (typeof renderReports === "function" && typeof state !== "undefined" && state.reports) {
    renderReports(state.reports);
  }
  if (typeof renderDestinations === "function" && typeof state !== "undefined" && state.destinations) {
    renderDestinations(state.destinations);
  }
  if (typeof loadPunctuality === "function") loadPunctuality();
}
