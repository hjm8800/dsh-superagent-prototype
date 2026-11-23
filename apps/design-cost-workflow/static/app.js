/* 设计造价工作流原型 · 前端交互（无第三方依赖）
   仅做渐进增强：删除二次确认、上传文件提示、防止表单重复提交。 */
(function () {
  "use strict";

  // 1) 危险操作二次确认：<button data-confirm="确认删除？">
  document.addEventListener("click", function (event) {
    var trigger = event.target.closest ? event.target.closest("[data-confirm]") : null;
    if (!trigger) return;
    if (!window.confirm(trigger.getAttribute("data-confirm") || "确认执行该操作？")) {
      event.preventDefault();
      event.stopPropagation();
    }
  });

  // 2) 上传文件：累计选择（分批选不同文件夹的文件会累加）+ 文件夹选择 + 数量/大小提示 + 清空
  var fileInput = document.querySelector("[data-file-input]");
  var dirInput = document.querySelector("[data-dir-input]");
  var relPathsField = document.querySelector("[data-rel-paths]");
  var fileHint = document.querySelector("[data-file-hint]");
  var fileClear = document.querySelector("[data-file-clear]");
  var fileCount = document.querySelector("[data-file-count]");
  if (fileInput && fileHint) {
    if (!fileHint.dataset.original) {
      fileHint.dataset.original = fileHint.innerHTML;
    }
    var picked = null;
    try {
      picked = new DataTransfer();
    } catch (err) {
      picked = null; // 浏览器不支持 DataTransfer 时退化为默认行为
    }

    var fileKey = function (file, path) {
      return (path || file.name || "") + "|" + (file.size || 0) + "|" + (file.lastModified || 0);
    };

    // 汇总两个输入框的选择：普通文件 + 文件夹（保留相对路径）
    var collect = function () {
      var items = [];
      var plain = fileInput.files || [];
      for (var i = 0; i < plain.length; i += 1) {
        items.push({ file: plain[i], path: "" });
      }
      if (dirInput) {
        var folder = dirInput.files || [];
        for (var j = 0; j < folder.length; j += 1) {
          items.push({ file: folder[j], path: folder[j].webkitRelativePath || folder[j].name || "" });
        }
      }
      return items;
    };

    var syncRelativePaths = function (items) {
      if (!relPathsField) return;
      var paths = items.map(function (item) { return item.path || ""; });
      relPathsField.value = JSON.stringify(paths);
    };

    var refresh = function () {
      var items = collect();
      var files = items.map(function (item) { return item.file; });
      syncRelativePaths(items);
      if (fileClear) {
        fileClear.hidden = files.length === 0;
      }
      if (!files.length) {
        fileHint.innerHTML = fileHint.dataset.original;
        if (fileCount) {
          fileCount.textContent = "";
        }
        return;
      }
      var total = 0;
      for (var i = 0; i < files.length; i += 1) {
        total += files[i].size || 0;
      }
      var sizeText = total > 1024 * 1024
        ? (total / 1024 / 1024).toFixed(2) + " MB"
        : Math.max(total / 1024, 0.1).toFixed(1) + " KB";
      fileHint.innerHTML = "<strong>已选择 " + files.length + " 个文件，共 " + sizeText +
        "（可继续选择其它文件夹的文件，会累计添加）</strong><br>" + fileHint.dataset.original;
      if (fileCount) {
        fileCount.textContent = "已选 " + files.length + " 个文件";
      }
    };

    fileInput.addEventListener("change", function () {
      if (!picked) {
        refresh();
        return;
      }
      var existing = {};
      for (var i = 0; i < picked.files.length; i += 1) {
        existing[fileKey(picked.files[i])] = true;
      }
      var incoming = fileInput.files || [];
      for (var j = 0; j < incoming.length; j += 1) {
        var file = incoming[j];
        var key = fileKey(file);
        if (!existing[key]) {
          picked.items.add(file);
          existing[key] = true;
        }
      }
      try {
        fileInput.files = picked.files; // 把累计结果写回 input，表单提交时包含全部文件
      } catch (err) {
        // 个别浏览器不允许赋值时保持默认（本次选择）行为
      }
      refresh();
    });

    if (dirInput) {
      dirInput.addEventListener("change", function () {
        // 选择文件夹：直接使用该文件夹下的全部文件（浏览器已展开），并保留相对路径
        if (picked) {
          try { fileInput.files = picked.files; } catch (err) { /* 忽略 */ }
        }
        refresh();
      });
    }

    if (fileClear) {
      fileClear.addEventListener("click", function () {
        picked = new DataTransfer();
        fileInput.value = "";
        if (dirInput) dirInput.value = "";
        refresh();
      });
    }
    refresh();
  }

  // 3) 防止重复提交（不改变按钮的 formaction 行为）
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form || form.dataset.submitting === "1") {
      if (form && form.dataset.submitting === "1") {
        event.preventDefault();
      }
      return;
    }
    form.dataset.submitting = "1";
    window.setTimeout(function () {
      form.dataset.submitting = "";
    }, 5000);
  }, true);

  // 4) 新增表单展开后滚动到可见区域
  document.addEventListener("click", function (event) {
    var summary = event.target.closest ? event.target.closest(".add-box > summary") : null;
    if (!summary) return;
    window.setTimeout(function () {
      summary.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }, 60);
  });
})();
