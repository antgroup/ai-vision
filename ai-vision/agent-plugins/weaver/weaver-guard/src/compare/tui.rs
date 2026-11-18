//! TUI for golden replay comparison using ratatui

use crate::compare::diff::{DiffResult, DiffType};
use crate::compare::engine::{CompareReport, CompareSummary};
use ratatui::{
    backend::CrosstermBackend,
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Style, Stylize},
    text::{Line, Span, Text},
    widgets::{Block, Borders, Paragraph, Wrap},
    Frame, Terminal,
};
use std::io;

/// Result from TUI session
pub struct TuiResult {
    /// Indices of reports that were fully accepted (replay should overwrite golden)
    pub accepted_reports: Vec<usize>,
}

/// Decision on a single diff
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DiffDecision {
    Pending,
    Accepted,
    Rejected,
}

/// State for the comparison TUI
pub struct CompareState {
    pub reports: Vec<CompareReport>,
    pub current_report_index: usize,
    pub current_diff_index: usize,
    pub diffs_shown: Vec<DiffResult>,
    pub decisions: Vec<DiffDecision>,
    /// Per-report: true if all diffs were accepted
    pub report_accepted: Vec<bool>,
    /// Per-report saved decisions (so navigating back restores previous choices)
    pub report_decisions: Vec<Vec<DiffDecision>>,
    /// Vertical scroll offset for the diff panel
    pub diff_scroll_offset: u16,
}

impl CompareState {
    pub fn new(reports: Vec<CompareReport>) -> Self {
        let report_count = reports.len();

        // Pre-compute per-report diffs and initial decisions
        let all_decisions: Vec<Vec<DiffDecision>> = reports
            .iter()
            .map(|r| {
                let count = Self::filter_diffs(&r.diffs).len();
                vec![DiffDecision::Pending; count]
            })
            .collect();

        let diffs_shown = if !reports.is_empty() {
            Self::filter_diffs(&reports[0].diffs)
        } else {
            Vec::new()
        };
        let decisions = if !all_decisions.is_empty() {
            all_decisions[0].clone()
        } else {
            Vec::new()
        };

        Self {
            reports,
            current_report_index: 0,
            current_diff_index: 0,
            diffs_shown,
            decisions,
            report_accepted: vec![false; report_count],
            report_decisions: all_decisions,
            diff_scroll_offset: 0,
        }
    }

    fn filter_diffs(diffs: &[DiffResult]) -> Vec<DiffResult> {
        diffs
            .iter()
            .filter(|d| d.is_meaningful())
            .cloned()
            .collect()
    }

    pub fn current_report(&self) -> Option<&CompareReport> {
        self.reports.get(self.current_report_index)
    }

    pub fn current_diff(&self) -> Option<&DiffResult> {
        self.diffs_shown.get(self.current_diff_index)
    }

    pub fn summary(&self) -> CompareSummary {
        self.current_report()
            .map(|r| r.summary())
            .unwrap_or_default()
    }

    pub fn move_next(&mut self) {
        if self.current_diff_index < self.diffs_shown.len().saturating_sub(1) {
            self.current_diff_index += 1;
        }
    }

    pub fn move_prev(&mut self) {
        if self.current_diff_index > 0 {
            self.current_diff_index -= 1;
        }
    }

    pub fn save_current_report_status(&mut self) {
        self.report_accepted[self.current_report_index] = self.current_report_all_accepted();
        self.report_decisions[self.current_report_index] = self.decisions.clone();
    }

    fn switch_to_report(&mut self, new_index: usize) {
        self.save_current_report_status();
        self.current_report_index = new_index;
        self.current_diff_index = 0;
        self.diff_scroll_offset = 0;
        self.diffs_shown = Self::filter_diffs(&self.reports[new_index].diffs);
        // Restore previously saved decisions instead of resetting to Pending
        self.decisions = self.report_decisions[new_index].clone();
    }

    pub fn next_report(&mut self) {
        if self.current_report_index < self.reports.len().saturating_sub(1) {
            self.switch_to_report(self.current_report_index + 1);
        }
    }

    pub fn prev_report(&mut self) {
        if self.current_report_index > 0 {
            self.switch_to_report(self.current_report_index - 1);
        }
    }

    pub fn scroll_down(&mut self) {
        self.diff_scroll_offset = self.diff_scroll_offset.saturating_add(1);
    }

    pub fn scroll_up(&mut self) {
        self.diff_scroll_offset = self.diff_scroll_offset.saturating_sub(1);
    }

    pub fn accept_current(&mut self) {
        if let Some(d) = self.decisions.get_mut(self.current_diff_index) {
            *d = DiffDecision::Accepted;
        }
        self.move_next();
    }

    pub fn reject_current(&mut self) {
        if let Some(d) = self.decisions.get_mut(self.current_diff_index) {
            *d = DiffDecision::Rejected;
        }
        self.move_next();
    }

    pub fn accept_all(&mut self) {
        self.decisions
            .iter_mut()
            .for_each(|d| *d = DiffDecision::Accepted);
    }

    pub fn reject_all(&mut self) {
        self.decisions
            .iter_mut()
            .for_each(|d| *d = DiffDecision::Rejected);
    }

    pub fn all_decided(&self) -> bool {
        self.decisions.iter().all(|d| *d != DiffDecision::Pending)
    }

    pub fn current_report_all_accepted(&self) -> bool {
        !self.decisions.is_empty() && self.decisions.iter().all(|d| *d == DiffDecision::Accepted)
    }

    pub fn total_reports(&self) -> usize {
        self.reports.len()
    }
}

/// Run the comparison TUI
pub fn run_tui(reports: Vec<CompareReport>) -> io::Result<TuiResult> {
    if reports.is_empty() {
        println!("No reports to display");
        return Ok(TuiResult {
            accepted_reports: vec![],
        });
    }

    // Enable raw mode and alternate screen for proper key event handling
    crossterm::terminal::enable_raw_mode()?;
    crossterm::execute!(std::io::stdout(), crossterm::terminal::EnterAlternateScreen)?;

    let result = run_tui_inner(reports);

    // Restore terminal state
    crossterm::execute!(std::io::stdout(), crossterm::terminal::LeaveAlternateScreen)?;
    crossterm::terminal::disable_raw_mode()?;

    result
}

fn run_tui_inner(reports: Vec<CompareReport>) -> io::Result<TuiResult> {
    let mut state = CompareState::new(reports);
    let mut terminal = Terminal::new(CrosstermBackend::new(std::io::stdout()))?;

    loop {
        terminal.draw(|f| render_frame(f, &state))?;

        // Handle input
        if let Ok(event) = crossterm::event::read() {
            match event {
                crossterm::event::Event::Key(key)
                    if key.kind == crossterm::event::KeyEventKind::Press =>
                {
                    let quit = match key.code {
                        crossterm::event::KeyCode::Char('q') => true,
                        crossterm::event::KeyCode::Char('j') | crossterm::event::KeyCode::Down => {
                            state.move_next();
                            // Each diff = 3 lines (path + golden + actual); sync scroll
                            state.diff_scroll_offset =
                                (state.current_diff_index as u16).saturating_mul(3);
                            false
                        }
                        crossterm::event::KeyCode::Char('k') | crossterm::event::KeyCode::Up => {
                            state.move_prev();
                            state.diff_scroll_offset =
                                (state.current_diff_index as u16).saturating_mul(3);
                            false
                        }
                        crossterm::event::KeyCode::Char('a') => {
                            state.accept_current();
                            false
                        }
                        crossterm::event::KeyCode::Char('A') => {
                            state.accept_all();
                            false
                        }
                        crossterm::event::KeyCode::Char('r') => {
                            state.reject_current();
                            false
                        }
                        crossterm::event::KeyCode::Char('R') => {
                            state.reject_all();
                            false
                        }
                        crossterm::event::KeyCode::Right | crossterm::event::KeyCode::Tab => {
                            state.next_report();
                            false
                        }
                        crossterm::event::KeyCode::Left | crossterm::event::KeyCode::BackTab => {
                            state.prev_report();
                            false
                        }
                        _ => false,
                    };

                    if quit {
                        // Save current report status before quitting
                        state.save_current_report_status();
                        let accepted_reports = state
                            .report_accepted
                            .iter()
                            .enumerate()
                            .filter(|(_, accepted)| **accepted)
                            .map(|(i, _)| i)
                            .collect();
                        return Ok(TuiResult { accepted_reports });
                    }
                }
                crossterm::event::Event::Resize(_, _) => {
                    // Redraw on resize
                }
                _ => {}
            }
        }
    }
}

/// Render the main TUI frame
fn render_frame(f: &mut Frame, state: &CompareState) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3), // Title
            Constraint::Min(10),   // Main content
            Constraint::Length(3), // Status bar
        ])
        .split(f.area());

    // Title bar
    let title = Paragraph::new("Golden Replay Compare")
        .style(Style::default().bold().cyan())
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(" Weaver Guard "),
        );
    f.render_widget(title, chunks[0]);

    // Main content
    render_main_content(f, chunks[1], state);

    // Status bar
    render_status_bar(f, chunks[2], state);
}

fn render_main_content(f: &mut Frame, area: Rect, state: &CompareState) {
    let chunks = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Min(30),        // Left: fixed min width
            Constraint::Percentage(80), // Right: take remaining space
        ])
        .split(area);

    // Left: File info and summary
    render_file_info(f, chunks[0], state);

    // Right: Diff list
    render_diff_list(f, chunks[1], state);
}

fn render_file_info(f: &mut Frame, area: Rect, state: &CompareState) {
    let report = match state.current_report() {
        Some(r) => r,
        None => {
            let para = Paragraph::new("No reports").block(Block::default().borders(Borders::ALL));
            f.render_widget(para, area);
            return;
        }
    };

    let summary = state.summary();

    let mut content = vec![
        Line::from(vec![
            Span::raw("Golden: "),
            Span::raw(report.golden_path.display().to_string()),
        ]),
        Line::from(vec![
            Span::raw("Actual: "),
            Span::raw(report.actual_path.display().to_string()),
        ]),
        Line::from(""),
        Line::from("Summary:"),
        Line::from(vec![
            Span::raw("  Modified: "),
            Span::raw(summary.modified.to_string()).red(),
        ]),
        Line::from(vec![
            Span::raw("  Added: "),
            Span::raw(summary.added.to_string()).green(),
        ]),
        Line::from(vec![
            Span::raw("  Removed: "),
            Span::raw(summary.removed.to_string()).yellow(),
        ]),
        Line::from(vec![
            Span::raw("  Unchanged: "),
            Span::raw(summary.unchanged.to_string()).green(),
        ]),
    ];

    // Total summary: all files with their diff counts
    if state.reports.len() > 1 {
        content.push(Line::from(""));
        content.push(Line::from(Span::raw("All Files:").bold()));
        for (i, r) in state.reports.iter().enumerate() {
            let name = r
                .golden_path
                .file_name()
                .unwrap_or_default()
                .to_string_lossy();
            let diff_count = r.diffs.iter().filter(|d| d.is_meaningful()).count();
            let marker = if i == state.current_report_index {
                "▸ "
            } else {
                "  "
            };
            let (count_span, color) = if diff_count > 0 {
                (format!("{} diffs", diff_count), Color::Yellow)
            } else {
                ("ok".to_string(), Color::Green)
            };
            content.push(Line::from(vec![
                Span::raw(marker),
                Span::raw(format!("{}", name)),
                Span::raw(format!(" ({})", count_span)).fg(color),
            ]));
        }
    }

    let para = Paragraph::new(Text::from(content))
        .wrap(Wrap { trim: false })
        .block(Block::default().borders(Borders::ALL).title(" Info "));
    f.render_widget(para, area);
}

fn render_diff_list(f: &mut Frame, area: Rect, state: &CompareState) {
    if state.diffs_shown.is_empty() {
        let para = Paragraph::new("No differences found!")
            .style(Style::default().green().bold())
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .title(" Differences "),
            );
        f.render_widget(para, area);
        return;
    }

    // Build lines for all diffs, with Golden/Actual on separate lines
    let mut lines: Vec<Line> = Vec::new();
    for (i, diff) in state.diffs_shown.iter().enumerate() {
        let decision = state
            .decisions
            .get(i)
            .copied()
            .unwrap_or(DiffDecision::Pending);

        let (symbol, color) = match decision {
            DiffDecision::Accepted => ("✓", Color::Green),
            DiffDecision::Rejected => ("✗", Color::Red),
            DiffDecision::Pending => match diff.diff_type {
                DiffType::Modified => ("M", Color::Yellow),
                DiffType::Added => ("+", Color::Green),
                DiffType::Removed => ("-", Color::Red),
                _ => (" ", Color::Gray),
            },
        };

        let cursor = if i == state.current_diff_index {
            "▸"
        } else {
            " "
        };

        // Path line
        lines.push(Line::from(vec![
            Span::raw(format!("{} [{}] ", cursor, symbol)).fg(color),
            Span::raw(&diff.path).bold(),
        ]));

        // Golden value on its own line
        lines.push(Line::from(vec![
            Span::raw("      Golden: ").fg(Color::DarkGray),
            Span::raw(diff.golden_value.as_deref().unwrap_or("(missing)")).fg(Color::Yellow),
        ]));

        // Actual value on its own line
        lines.push(Line::from(vec![
            Span::raw("      Actual: ").fg(Color::DarkGray),
            Span::raw(diff.actual_value.as_deref().unwrap_or("(missing)")).fg(Color::Green),
        ]));
    }

    let para = Paragraph::new(Text::from(lines))
        .wrap(Wrap { trim: false })
        .scroll((state.diff_scroll_offset, 0))
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(" Differences "),
        );

    f.render_widget(para, area);
}

fn render_status_bar(f: &mut Frame, area: Rect, state: &CompareState) {
    let (status_text, status_color) = if state.summary().has_changes() {
        (
            format!("❌ FAILED ({} changes)", state.summary().total_changes()),
            Color::Red,
        )
    } else {
        ("✅ PASSED".to_string(), Color::Green)
    };

    let report_num = state.current_report_index + 1;
    let total = state.total_reports();

    // Show per-report acceptance progress
    let accepted_count = state
        .decisions
        .iter()
        .filter(|d| **d == DiffDecision::Accepted)
        .count();
    let total_diffs = state.decisions.len();

    let content = vec![Line::from(vec![
        Span::raw("Status: "),
        Span::raw(status_text).fg(status_color),
        Span::raw(" | "),
        Span::raw(format!("File {}/{}", report_num, total)),
        Span::raw(" | "),
        Span::raw(format!("Accepted {}/{} ", accepted_count, total_diffs)).fg(
            if accepted_count == total_diffs && total_diffs > 0 {
                Color::Green
            } else {
                Color::Yellow
            },
        ),
        Span::raw(" [←→]File [↑↓/jk]Nav "),
        Span::raw("[a]Accept [A]AcceptAll [r]Reject [R]RejectAll "),
        Span::raw("[q]Quit"),
    ])];

    let para = Paragraph::new(Text::from(content))
        .style(Style::default().dark_gray())
        .block(Block::default().borders(Borders::ALL));
    f.render_widget(para, area);
}

/// Render diff output in text format (non-interactive)
pub fn render_text_diff(reports: &[CompareReport]) -> String {
    let mut output = String::new();

    for report in reports {
        output.push_str(&format!(
            "=== {} vs {} ===\n",
            report.golden_path.display(),
            report.actual_path.display()
        ));

        let summary = report.summary();
        output.push_str(&format!(
            "Summary: Modified={}, Added={}, Removed={}, Unchanged={}\n\n",
            summary.modified, summary.added, summary.removed, summary.unchanged
        ));

        for diff in &report.diffs {
            if !diff.is_meaningful() {
                continue;
            }

            let symbol = match diff.diff_type {
                DiffType::Modified => "[M]",
                DiffType::Added => "[+]",
                DiffType::Removed => "[-]",
                _ => "[ ]",
            };

            output.push_str(&format!("{} {}\n", symbol, diff.path));
            output.push_str(&format!(
                "    Golden: {}\n",
                diff.golden_value.as_deref().unwrap_or("(missing)")
            ));
            output.push_str(&format!(
                "    Actual: {}\n\n",
                diff.actual_value.as_deref().unwrap_or("(missing)")
            ));
        }

        output.push_str("---\n\n");
    }

    output
}

/// Render diff output in JSON format
pub fn render_json_diff(reports: &[CompareReport]) -> String {
    let output: Vec<serde_json::Value> = reports
        .iter()
        .map(|report| {
            let diffs: Vec<serde_json::Value> = report
                .diffs
                .iter()
                .filter(|d| d.is_meaningful())
                .map(|d| {
                    serde_json::json!({
                        "path": d.path,
                        "golden_value": d.golden_value,
                        "actual_value": d.actual_value,
                        "diff_type": d.diff_type.label(),
                    })
                })
                .collect();

            serde_json::json!({
                "golden_path": report.golden_path.to_string_lossy(),
                "actual_path": report.actual_path.to_string_lossy(),
                "passed": report.passed,
                "summary": {
                    "modified": report.summary().modified,
                    "added": report.summary().added,
                    "removed": report.summary().removed,
                    "unchanged": report.summary().unchanged,
                },
                "diffs": diffs,
            })
        })
        .collect();

    serde_json::to_string_pretty(&output).unwrap_or_else(|_| "[]".to_string())
}
