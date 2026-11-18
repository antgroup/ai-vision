//go:build !linux

package sandboxbootstrap

func readLocalEnvironment() localEnvironment { return localEnvironment{} }
