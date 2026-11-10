/* Offline text faces shared by conversation and Moments archives. */
(() => {
  function append(parent, value) {
    const text = String(value ?? ''), faces = window.MoectInlineFaces || {};
    const pattern = /https?:\/\/[^\s<>"']+|\[[^\[\]\r\n]{1,32}\]/g;
    let offset = 0;
    for (const match of text.matchAll(pattern)) {
      parent.append(document.createTextNode(text.slice(offset, match.index)));
      const path = Object.hasOwn(faces, match[0]) ? faces[match[0]] : '';
      if (typeof path === 'string' && /^builtin-faces\/[a-f0-9]{20}\.png$/.test(path)) {
        const image = document.createElement('img');
        image.className = 'moect-inline-face';
        image.src = path; image.alt = match[0]; image.title = match[0]; image.draggable = false;
        image.style.cssText = 'display:inline-block;width:1.4em;height:1.4em;vertical-align:-.25em;object-fit:contain;margin:0 .04em;border-radius:0';
        image.onerror = () => image.replaceWith(document.createTextNode(match[0]));
        parent.append(image);
      } else parent.append(document.createTextNode(match[0]));
      offset = match.index + match[0].length;
    }
    parent.append(document.createTextNode(text.slice(offset)));
  }
  window.MoectFaces = {append};
})();
