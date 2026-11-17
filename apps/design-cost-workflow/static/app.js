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

  // 2) 上传文件数量 / 大小提示
  var fileInput = document.querySelector("[data-file-input]");
  var fileHint = document.querySelector("[data-file-hint]");
  if (fileInput && fileHint) {
    if (!fileHint.dataset.original) {
      fileHint.dataset.original = fileHint.innerHTML;
    }
    fileInput.addEventListener("change", function () {
      var files = fileInput.files || [];
      if (!files.length) {
        fileHint.innerHTML = fileHint.dataset.original;
        return;
      }
      var total = 0;
      for (var i = 0; i < files.length; i += 1) {
        total += files[i].size || 0;
      }
      var sizeText = total > 1024 * 1024
        ? (total / 1024 / 1024).toFixed(2) + " MB"
        : Math.max(total / 1024, 0.1).toFixed(1) + " KB";
      fileHint.innerHTML = "<strong>已选择 " + files.length + " 个文件，共 " + sizeText + "</strong><br>" +
        fileHint.dataset.original;
    });
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
