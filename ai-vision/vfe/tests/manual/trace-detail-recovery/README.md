# Trace detail recovery acceptance

Run from `ai-vision/vfe`:

```sh
npx vite --config tests/manual/trace-detail-recovery/vite.config.ts
```

Open `http://127.0.0.1:64710/tests/manual/trace-detail-recovery/index.html`.
This mounts the real native Trace drawer and IO components with synthetic data.
The dedicated Vite config replaces only the drawer's detail import with a wrapper
that throws for the `fault` span; production configuration never loads it.
No backend, credentials, SDK, or model requests are involved.

1. Check absent content says Output not recorded without a copy action. Other cases preserve their
   display/copy semantics in JSON and Formatted modes.
2. Open the trace and select the simulated faulty call. Only details should show
   the localized fallback; tree, search, view controls, and Back to trace remain.
3. Select a healthy call; its actual details must render. Return to the faulty
   call and retry: this entry simulates the underlying failure clearing at retry.
4. Add `?persistent=1` to verify repeated retries remain bounded to details.
5. Close, switch trace, and reopen. Repeat with `?lang=en-US` or `?lang=ja-JP`,
   `&theme=dark` and both detail formatting modes.

6. Select the call without input/output. The right panel should name the absent
   side and state that this does not imply failure. Help is collapsed by default.
   Expand it with a click or Enter to see possible causes and troubleshooting.
7. Select input-only, output-only and explicit-empty calls. Preserve recorded
   content; show the missing side only. Output timing advice appears only when
   output is absent, and attribute advice only when attributes are available.
   Explicit null and zero must not get a missing-content notice.
8. Check Chinese light, English dark and Japanese dark at 1024×768; expanded
   help should wrap without horizontal clipping. Switching calls resets it.

Do not describe this controlled exception injection as a reproduced live bug.
