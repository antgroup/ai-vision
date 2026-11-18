//go:build linux

package sandboxbootstrap

import "syscall"

func readLocalEnvironment() localEnvironment {
	result := localEnvironment{Linux: true}
	var name syscall.Utsname
	if err := syscall.Uname(&name); err != nil {
		result.Err = err
		return result
	}
	for _, c := range name.Release {
		if c == 0 {
			break
		}
		result.Kernel += string(rune(c))
	}
	abi, _, errno := syscall.RawSyscall(444, 0, 0, 1)
	if errno != 0 {
		result.Err = errno
		return result
	}
	result.ABI = int(abi)
	return result
}
