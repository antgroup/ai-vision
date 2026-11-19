package auth

import (
	"fmt"
	"os"
	"syscall"
)

var setConsoleMode = syscall.NewLazyDLL("kernel32.dll").NewProc("SetConsoleMode")

func PromptSecret(label string) (string, error) {
	handle := os.Stdin.Fd()
	var mode uint32
	if err := syscall.GetConsoleMode(syscall.Handle(handle), &mode); err != nil {
		return "", fmt.Errorf("%s requires a terminal; use the corresponding stdin flag", label)
	}
	const enableEchoInput = 4
	if ok, _, err := setConsoleMode.Call(handle, uintptr(mode&^enableEchoInput)); ok == 0 {
		return "", fmt.Errorf("cannot disable terminal echo: %w", err)
	}
	defer func() {
		_, _, _ = setConsoleMode.Call(handle, uintptr(mode))
		fmt.Fprintln(os.Stderr)
	}()
	fmt.Fprintf(os.Stderr, "%s: ", label)
	return readLine(os.Stdin)
}

func Interactive() bool {
	var mode uint32
	return syscall.GetConsoleMode(syscall.Handle(os.Stdin.Fd()), &mode) == nil
}
