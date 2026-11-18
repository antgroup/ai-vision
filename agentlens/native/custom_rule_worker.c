/* Native entry point for the compiled custom-rule protocol implementation.
 * Match `python -I -S`: ignore environment/user-site and never execute site.py.
 * The only additional search path is the executable's own installation directory.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    char executable[PATH_MAX];
    ssize_t length = readlink("/proc/self/exe", executable, sizeof(executable) - 1);
    if (length < 0) {
        perror("agentlens-rule-worker: executable path");
        return 1;
    }
    executable[length] = '\0';
    char *separator = strrchr(executable, '/');
    if (separator == NULL) return 1;
    *separator = '\0';

    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);
    config.site_import = 0;
    PyStatus status = PyConfig_SetString(&config, &config.program_name,
                                         L"/usr/local/bin/python3.11");
    if (!PyStatus_Exception(status)) status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) Py_ExitStatusException(status);

    PyObject *flags = PySys_GetObject("flags"); /* Borrowed. */
    const char *names[] = {"isolated", "no_site", "ignore_environment", "no_user_site"};
    for (size_t index = 0; index < sizeof(names) / sizeof(names[0]); index++) {
        PyObject *flag = PyObject_GetAttrString(flags, names[index]);
        int enabled = flag != NULL && PyObject_IsTrue(flag) == 1;
        Py_XDECREF(flag);
        if (!enabled) {
            fprintf(stderr, "agentlens-rule-worker: isolation flag missing: %s\n", names[index]);
            return 1;
        }
    }
    if (argc == 2 && strcmp(argv[1], "--check-isolation") == 0) {
        puts("{\"isolated\":1,\"no_site\":1,\"ignore_environment\":1,\"no_user_site\":1}");
        return Py_FinalizeEx() < 0 ? 1 : 0;
    }
    PyObject *directory = PyUnicode_DecodeFSDefault(executable);
    if (directory == NULL || PyList_Append(PySys_GetObject("path"), directory) < 0) {
        Py_XDECREF(directory);
        PyErr_Print();
        return 1;
    }
    Py_DECREF(directory);
    PyObject *module = PyImport_ImportModule("agentlens_rule_worker_impl");
#ifdef AGENTLENS_SANDBOX_WORKER
    const char *entrypoint = "sandbox_main";
#else
    const char *entrypoint = "main";
#endif
    PyObject *result = module == NULL ? NULL : PyObject_CallMethod(module, entrypoint, NULL);
    Py_XDECREF(module);
    if (result == NULL) {
        PyErr_Print();
        return 1;
    }
    Py_DECREF(result);
    return Py_FinalizeEx() < 0 ? 1 : 0;
}
