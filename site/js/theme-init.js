// 表示のちらつきを防ぐため <head> で同期的に読み込む
(function () {
  try {
    var theme = localStorage.getItem("site-theme");
    if (theme === "light" || theme === "dark") {
      document.documentElement.setAttribute("data-theme", theme);
    }
  } catch (e) {}
})();
