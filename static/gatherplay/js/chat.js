/* Chat: send without reloading and check for new messages every few seconds. */
(function () {
  "use strict";

  var chat = document.querySelector("[data-chat]");
  if (!chat) return;

  var log = chat.querySelector("[data-chat-log]");
  var form = chat.querySelector("[data-chat-form]");
  var textarea = form.querySelector("textarea");
  var button = form.querySelector("button");
  var pollUrl = chat.dataset.pollUrl;
  var POLL_MS = 4000;
  var busy = false;

  function lastId() {
    var items = log.querySelectorAll("[data-id]");
    return items.length ? items[items.length - 1].dataset.id : 0;
  }

  function scrollToEnd() {
    log.scrollTop = log.scrollHeight;
  }

  function append(message) {
    if (log.querySelector('[data-id="' + message.id + '"]')) return;
    var empty = log.querySelector("[data-chat-empty]");
    if (empty) empty.remove();
    var item = document.createElement("li");
    item.className = "chat-msg" + (message.mine ? " is-mine" : "");
    item.dataset.id = message.id;
    var bubble = document.createElement("p");
    bubble.className = "chat-bubble";
    bubble.textContent = message.body; // textContent keeps member text from being read as HTML
    var meta = document.createElement("span");
    meta.className = "chat-meta";
    meta.textContent = (message.mine ? "" : message.sender + " · ") + message.time;
    item.appendChild(bubble);
    item.appendChild(meta);
    log.appendChild(item);
  }

  function poll() {
    if (busy) return Promise.resolve();
    busy = true;
    return fetch(pollUrl + "?after=" + lastId(), {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    })
      .then(function (response) { return response.ok ? response.json() : { messages: [] }; })
      .then(function (data) {
        var nearEnd = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
        data.messages.forEach(append);
        if (data.messages.length && nearEnd) scrollToEnd();
      })
      .catch(function () {})
      .then(function () { busy = false; });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (!textarea.value.trim()) return;
    button.disabled = true;
    fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    })
      .then(function (response) {
        if (!response.ok) throw new Error("send failed");
        textarea.value = "";
        return poll().then(scrollToEnd);
      })
      .catch(function () { form.submit(); }) // fall back to a normal page post
      .then(function () {
        button.disabled = false;
        textarea.focus();
      });
  });

  // Enter sends; Shift+Enter adds a new line.
  textarea.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  scrollToEnd();
  setInterval(function () {
    if (!document.hidden) poll();
  }, POLL_MS);
})();
