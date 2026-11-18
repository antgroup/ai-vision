/* Linux CPython launcher for compiled Alembic environment and revision modules. */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#ifndef AGENTLENS_ENTRY_MODULE
#define AGENTLENS_ENTRY_MODULE "agentlens_migrations"
#endif
#ifndef AGENTLENS_ENTRY_FUNCTION
#define AGENTLENS_ENTRY_FUNCTION "main"
#endif

int main(int argc, char **argv) {
    char executable[PATH_MAX];
    ssize_t length = readlink("/proc/self/exe", executable, sizeof(executable) - 1);
    if (length < 0) {
        perror("agentlens-migrate: executable path");
        return 1;
    }
    executable[length] = '\0';
    char *separator = strrchr(executable, '/');
    if (separator == NULL) return 1;
    *separator = '\0';
    char interpreter[PATH_MAX];
    char modules[PATH_MAX];
    if (snprintf(interpreter, sizeof(interpreter), "%s/.venv/bin/python", executable)
            >= (int)sizeof(interpreter) ||
        snprintf(modules, sizeof(modules), "%s/src", executable) >= (int)sizeof(modules)) {
        return 1;
    }
    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    config.parse_argv = 0;
    PyStatus status = PyConfig_SetBytesString(&config, &config.program_name, interpreter);
    if (!PyStatus_Exception(status)) {
        status = PyConfig_SetBytesString(&config, &config.executable, interpreter);
    }
    if (!PyStatus_Exception(status)) status = PyConfig_SetBytesArgv(&config, argc, argv);
    if (!PyStatus_Exception(status)) status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) Py_ExitStatusException(status);
    PyObject *directory = PyUnicode_DecodeFSDefault(modules);
    if (directory == NULL || PyList_Insert(PySys_GetObject("path"), 0, directory) < 0) {
        Py_XDECREF(directory);
        PyErr_Print();
        return 1;
    }
    Py_DECREF(directory);
    PyObject *module = PyImport_ImportModule(AGENTLENS_ENTRY_MODULE);
    PyObject *result = module == NULL ? NULL : PyObject_CallMethod(module, AGENTLENS_ENTRY_FUNCTION, NULL);
    Py_XDECREF(module);
    if (result == NULL) {
        PyErr_Print();
        Py_FinalizeEx();
        return 1;
    }
    Py_DECREF(result);
    return Py_FinalizeEx() < 0 ? 120 : 0;
}
