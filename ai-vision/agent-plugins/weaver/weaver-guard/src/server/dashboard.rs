pub const HTML: &str = include_str!("dashboard.html");
pub const CSS: &str = include_str!("dashboard.css");
pub const JS: &str = concat!(
    include_str!("dashboard-constants.js"),
    "\n",
    include_str!("dashboard-utils.js"),
    "\n",
    include_str!("dashboard-prompt.js"),
    "\n",
    include_str!("dashboard-semantic.js"),
    "\n",
    include_str!("dashboard-render.js"),
    "\n",
    include_str!("dashboard-app.js"),
    "\n",
);
