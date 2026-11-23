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

  // 2) 上传文件：累计选择（分批选不同文件夹的文件会累加）+ 数量/大小提示 + 清空
  var fileInput = document.querySelector("[data-file-input]");
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

    var fileKey = function (file) {
      return (file.name || "") + "|" + (file.size || 0) + "|" + (file.lastModified || 0);
    };

    var refresh = function () {
      var files = fileInput.files || [];
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
      var added = 0;
      var incoming = fileInput.files || [];
      for (var j = 0; j < incoming.length; j += 1) {
        var file = incoming[j];
        var key = fileKey(file);
        if (!existing[key]) {
          picked.items.add(file);
          existing[key] = true;
          added += 1;
        }
      }
      try {
        fileInput.files = picked.files; // 把累计结果写回 input，表单提交时包含全部文件
      } catch (err) {
        // 个别浏览器不允许赋值时保持默认（本次选择）行为
      }
      refresh();
    });

    if (fileClear) {
      fileClear.addEventListener("click", function () {
        picked = new DataTransfer();
        fileInput.value = "";
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
