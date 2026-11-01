/* Internal inbox helpers. Public Live Chat does not load this composer. */
(() => {
  let viewer;
  let viewerOpener;
  function showAttachment(item, opener) {
    if (!viewer) {
      viewer = document.createElement("dialog");
      viewer.className = "internal-media-dialog";
      viewer.dataset.internalMediaDialog = "";
      viewer.setAttribute("aria-labelledby", "internal-media-title");
      const header = document.createElement("header");
      const title = document.createElement("h2");
      title.id = "internal-media-title";
      const close = document.createElement("button");
      close.type = "button";
      close.className = "internal-media-close";
      close.setAttribute("aria-label", "Close attachment");
      close.textContent = "×";
      close.addEventListener("click", () => viewer.close());
      const body = document.createElement("div");
      body.className = "internal-media-viewer";
      header.append(title, close);
      viewer.append(header, body);
      document.body.append(viewer);
      viewer.addEventListener("click", event => { if (event.target === viewer) {
        const rect = viewer.getBoundingClientRect();
        if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) viewer.close();
      }});
      viewer.addEventListener("close", () => {
        viewer.querySelector(".internal-media-viewer").replaceChildren();
        viewerOpener?.focus({preventScroll:true});
      });
    }
    viewerOpener = opener;
    viewer.querySelector("h2").textContent = item.filename;
    const media = document.createElement(item.content_type.startsWith("video/") ? "video" : "img");
    media.src = `/portal/conversation-attachments/${Number(item.attachment_id)}`;
    if (media instanceof HTMLVideoElement) { media.controls = true; media.preload = "metadata"; }
    else media.alt = item.filename;
    viewer.querySelector(".internal-media-viewer").replaceChildren(media);
    if (!viewer.open) viewer.showModal();
    viewer.querySelector("button").focus();
  }

  function renderAttachments(row, attachments = []) {
    row.classList.toggle("is-media-only", attachments.length > 0 && !row.querySelector("p"));
    for (const item of attachments) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "internal-message-attachment";
      button.setAttribute("aria-label", `View attachment: ${item.filename}`);
      const media = document.createElement(item.content_type.startsWith("video/") ? "video" : "img");
      media.src = `/portal/conversation-attachments/${Number(item.attachment_id)}`;
      if (media instanceof HTMLVideoElement) { media.preload = "metadata"; media.muted = true; }
      else { media.alt = item.filename; media.loading = "lazy"; }
      const label = document.createElement("span");
      label.textContent = item.filename;
      button.append(media, label);
      button.addEventListener("click", () => showAttachment(item, button));
      row.append(button);
    }
  }

  function renderAvatar(target, person = {}, prefix = "") {
    const path = person[`${prefix}profile_image_path`] || "";
    const signature = [path,person[`${prefix}profile_image_position_x`],person[`${prefix}profile_image_position_y`],person[`${prefix}profile_image_scale`]].join("|");
    if (target.dataset.profileSignature === signature) return;
    target.dataset.profileSignature = signature;
    target.classList.add("internal-person-avatar");
    target.replaceChildren();
    const fallback = () => {
      const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      icon.setAttribute("viewBox", "0 0 24 24");
      icon.setAttribute("fill", "none");
      icon.setAttribute("stroke", "currentColor");
      icon.setAttribute("stroke-width", "1.8");
      icon.setAttribute("aria-hidden", "true");
      const circle = document.createElementNS(icon.namespaceURI, "circle");
      circle.setAttribute("cx", "12"); circle.setAttribute("cy", "8"); circle.setAttribute("r", "4");
      const shoulders = document.createElementNS(icon.namespaceURI, "path");
      shoulders.setAttribute("d", "M4 21v-2a8 8 0 0 1 16 0v2");
      icon.append(circle, shoulders);
      target.replaceChildren(icon);
    };
    if (!path.startsWith("uploads/profiles/")) { fallback(); return; }
    const photo = document.createElement("img");
    photo.src = `/static/${path.split("/").map(encodeURIComponent).join("/")}`;
    photo.alt = "";
    photo.style.objectPosition = `${Number(person[`${prefix}profile_image_position_x`] ?? 50)}% ${Number(person[`${prefix}profile_image_position_y`] ?? 50)}%`;
    photo.style.transform = `scale(${Math.max(1, Math.min(3, Number(person[`${prefix}profile_image_scale`] ?? 1)))})`;
    photo.addEventListener("error", fallback, {once:true});
    target.append(photo);
  }

  function controls(form) {
    form.dataset.asyncForm="";
    const text = form.querySelector("textarea");
    text.required = false;
    text.addEventListener("keydown", event => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        if (!text.disabled && !form.querySelector('[type="submit"]')?.disabled) form.requestSubmit();
      }
    });
    const input = document.createElement("input");
    input.type = "file";
    input.multiple = true;
    input.accept = "image/jpeg,image/png,image/webp,video/mp4,video/webm";
    input.className = "sr-only";
    input.setAttribute("aria-label","Attach images or videos");
    const label = document.createElement("label");
    label.className = "internal-media-picker";
    const caption = document.createElement("span");
    caption.textContent = "+ Media";
    label.append(caption,input);
    form.prepend(label);
    const previews = document.createElement("div");
    previews.className = "internal-media-previews";
    previews.setAttribute("aria-live","polite");
    previews.hidden = true;
    form.before(previews);
    let urls = [];
    function clear() {
      urls.forEach(URL.revokeObjectURL);
      urls = [];
      input.value = "";
      previews.replaceChildren();
      previews.hidden = true;
    }
    function renderPreviews() {
      urls.forEach(URL.revokeObjectURL);
      urls = [];
      const files = [...input.files];
      const invalid = files.length>3 || files.reduce((sum,file)=>sum+file.size,0)>30*1024*1024 || files.some(file =>
        !input.accept.split(",").includes(file.type) || file.size>(file.type.startsWith("video/")?25:5)*1024*1024);
      previews.replaceChildren();
      previews.hidden = !files.length;
      if (invalid) {
        input.value = "";
        previews.textContent = "Choose up to 3 files: images up to 5 MB, videos up to 25 MB, 30 MB total.";
        return;
      }
      files.forEach((file, index) => {
        const figure = document.createElement("figure");
        const media = document.createElement(file.type.startsWith("video/")?"video":"img");
        const url = URL.createObjectURL(file);
        urls.push(url);
        media.src = url;
        if (media instanceof HTMLVideoElement) { media.controls = true; media.preload = "metadata"; }
        else media.alt = file.name;
        const name = document.createElement("figcaption");
        name.textContent = file.name;
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "internal-media-remove";
        remove.setAttribute("aria-label", `Remove ${file.name}`);
        remove.textContent = "×";
        remove.disabled = input.disabled;
        remove.addEventListener("click", () => {
          if (input.disabled) return;
          const remaining = new DataTransfer();
          files.forEach((item, position) => { if (position !== index) remaining.items.add(item); });
          input.files = remaining.files;
          renderPreviews();
          (previews.querySelector("button") || input).focus();
        });
        figure.append(media,name,remove);
        previews.append(figure);
      });
    }
    input.addEventListener("change", renderPreviews);
    return {
      clear,
      hasFiles:()=>input.files.length>0,
      signature:()=>[...input.files].map(f=>`${f.name}:${f.size}:${f.lastModified}`).join("|"),
      disable:value=>{input.disabled=value; previews.querySelectorAll("button").forEach(button=>{button.disabled=value;});},
      body(fields) {
        if (!input.files.length) return JSON.stringify(fields);
        const data = new FormData();
        Object.entries(fields).forEach(([key,value])=>{if(value!==null && value!==undefined)data.append(key,String(value));});
        [...input.files].forEach(file=>data.append("attachments",file));
        return data;
      },
    };
  }
  window.InternalConversationMedia = {controls,renderAttachments,renderAvatar};

  const nav = document.querySelector('.portal-nav a[href$="/messages"], .portal-nav a[href$="/inbox"]');
  if (!nav) return;
  function badge(element,count) {
    if (!element) return;
    let mark = element.querySelector("[data-internal-unread], :scope > b");
    if (!mark) {
      mark=document.createElement("b");
      mark.dataset.internalUnread="";
      mark.className="conversation-unread-badge";
      element.append(mark);
    }
    mark.dataset.internalUnread="";
    mark.classList.add("conversation-unread-badge");
    mark.hidden=!count;
    mark.textContent=count>99?"99+":String(count);
    mark.setAttribute("aria-label",`${count} unread ${element===nav?"conversations":"messages"}`);
    element.classList.toggle("has-unread",Boolean(count));
  }
  let busy=false;
  window.refreshConversationBadges=async()=>{
    if (busy || document.hidden) return;
    busy=true;
    try {
      const response=await fetch("/api/portal/conversations/unread",{headers:{Accept:"application/json"},cache:"no-store"});
      if (!response.ok) return;
      const result=await response.json();
      badge(nav,result.unread_conversations);
      if (result.threads) {
        const counts=new Map(result.threads.map(row=>[String(row.reseller_account_id),row.unread_count]));
        document.querySelectorAll("[data-reseller-id]").forEach(row=>badge(row,counts.get(row.dataset.resellerId)||0));
      }
      if (result.orders) {
        badge(document.querySelector("[data-reseller-conversation-open]"),result.sales_unread_count);
        const counts=new Map(result.orders.map(row=>[`order-${row.order_id}`,row.unread_count]));
        document.querySelectorAll("[data-system-conversation-open]").forEach(row=>badge(row,counts.get(row.dataset.systemOrderKey)||0));
      }
      document.dispatchEvent(new CustomEvent("conversations:unread",{detail:result}));
    } catch { /* Keep the last known badge until the next successful refresh. */ }
    finally {busy=false;}
  };
  window.refreshConversationBadges();
  const timer=setInterval(window.refreshConversationBadges,15000);
  window.addEventListener("focus",window.refreshConversationBadges);
  document.addEventListener("visibilitychange",()=>{if(!document.hidden)window.refreshConversationBadges();});
  window.addEventListener("pagehide",()=>clearInterval(timer),{once:true});
})();
