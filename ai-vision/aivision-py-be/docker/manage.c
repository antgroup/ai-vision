/* Build-time CPython launcher for the compiled management module. */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#ifndef AIVISION_ENTRY_MODULE
#define AIVISION_ENTRY_MODULE "manage"
#endif
#ifndef AIVISION_ENTRY_FUNCTION
#define AIVISION_ENTRY_FUNCTION "main"
#endif

int main(int argc, char **argv)
{
    char executable[PATH_MAX];
    ssize_t length = readlink("/proc/self/exe", executable, sizeof(executable) - 1);
    if (length < 0) {
        perror("locate management launcher");
        return 1;
    }
    executable[length] = '\0';
    char *separator = strrchr(executable, '/');
    if (separator == NULL) {
        return 1;
    }
    *separator = '\0';

    char interpreter[PATH_MAX];
    if (snprintf(interpreter, sizeof(interpreter), "%s/.venv/bin/python", executable)
            >= (int)sizeof(interpreter)) {
        return 1;
    }

    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    config.parse_argv = 0;
    PyStatus status = PyConfig_SetBytesString(&config, &config.program_name, interpreter);
    if (!PyStatus_Exception(status)) {
        status = PyConfig_SetBytesString(&config, &config.executable, interpreter);
    }
    if (!PyStatus_Exception(status)) {
        status = PyConfig_SetBytesArgv(&config, argc, argv);
    }
    if (!PyStatus_Exception(status)) {
        status = Py_InitializeFromConfig(&config);
    }
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) {
        Py_ExitStatusException(status);
    }

    PyObject *directory = PyUnicode_DecodeFSDefault(executable);
    if (directory == NULL || PyList_Insert(PySys_GetObject("path"), 0, directory) < 0) {
        Py_XDECREF(directory);
        PyErr_Print();
        return 1;
    }
    Py_DECREF(directory);
    PyObject *module = PyImport_ImportModule(AIVISION_ENTRY_MODULE);
    PyObject *result = module == NULL ? NULL : PyObject_CallMethod(module, AIVISION_ENTRY_FUNCTION, NULL);
    Py_XDECREF(module);
    if (result == NULL) {
        /* PyErr_Print preserves SystemExit status from Django's CLI parser. */
        PyErr_Print();
        Py_FinalizeEx();
        return 1;
    }
    Py_DECREF(result);
    return Py_FinalizeEx() < 0 ? 120 : 0;
}
