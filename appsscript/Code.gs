/**
 * Sends whatever the tracker left in the Outbox tab.
 *
 * This exists so the tracker never needs a Gmail app password. It runs as
 * whoever installs it, using the sheet's own permissions, and can only send
 * mail. It cannot read your inbox.
 *
 * Install once:
 *   1. Open the sheet, Extensions > Apps Script.
 *   2. Paste this file in, replacing whatever is there. Save.
 *   3. Run `sendOutbox` once by hand and approve the permission prompt.
 *   4. Run `createTrigger` once. That schedules it every morning.
 *
 * Gmail allows 100 messages a day on a consumer account, so two a day is fine.
 */

var OUTBOX_TAB = 'Outbox';
var TRIGGER_HOUR = 7;   // runs between 7 and 8am, after the plan is generated
var MAX_AGE_HOURS = 20; // never send a plan that has gone stale

function sendOutbox() {
  var sheet = SpreadsheetApp.getActive().getSheetByName(OUTBOX_TAB);
  if (!sheet) return;

  var rows = sheet.getDataRange().getValues();
  var today = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd');

  for (var i = 1; i < rows.length; i++) {
    var queued = rows[i][0];
    var sendAfter = rows[i][1];
    var to = rows[i][2];
    var subject = rows[i][3];
    var status = rows[i][4];
    var html = rows[i][5];

    if (status !== 'queued' || !to || !html) continue;

    // Only today's message. A run that failed yesterday stays unsent.
    var stamp = (sendAfter instanceof Date)
      ? Utilities.formatDate(sendAfter, Session.getScriptTimeZone(), 'yyyy-MM-dd')
      : String(sendAfter).slice(0, 10);
    if (stamp !== today) {
      sheet.getRange(i + 1, 5).setValue('skipped, stale');
      continue;
    }

    if (queued instanceof Date) {
      var ageHours = (new Date() - queued) / 36e5;
      if (ageHours > MAX_AGE_HOURS) {
        sheet.getRange(i + 1, 5).setValue('skipped, stale');
        continue;
      }
    }

    try {
      MailApp.sendEmail({
        to: to,
        subject: subject,
        htmlBody: html,
        name: 'Solids'
      });
      sheet.getRange(i + 1, 5).setValue('sent ' + new Date().toISOString());
    } catch (err) {
      sheet.getRange(i + 1, 5).setValue('failed: ' + err.message);
    }
  }

  pruneOldRows_(sheet);
}

/** Keep the tab from growing without limit. The HTML rows are large. */
function pruneOldRows_(sheet) {
  var keep = 30;
  var extra = sheet.getLastRow() - 1 - keep;
  if (extra > 0) sheet.deleteRows(2, extra);
}

function createTrigger() {
  var existing = ScriptApp.getProjectTriggers();
  for (var i = 0; i < existing.length; i++) {
    if (existing[i].getHandlerFunction() === 'sendOutbox') {
      ScriptApp.deleteTrigger(existing[i]);
    }
  }
  ScriptApp.newTrigger('sendOutbox')
    .timeBased()
    .atHour(TRIGGER_HOUR)
    .everyDays(1)
    .create();
}
