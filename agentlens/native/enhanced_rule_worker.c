/* Dedicated isolated launcher: never exposes the AgentLens service path/venv. */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    if (argc > 2 || (argc == 2 && strcmp(argv[1], "--check-isolation") != 0 &&
                    strcmp(argv[1], "--check-runtime") != 0)) return 2;
    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);
    config.site_import = 0;
    config.write_bytecode = 0;
    PyStatus status = PyConfig_SetString(&config, &config.program_name,
                                        L"/usr/local/bin/python3.11");
    if (!PyStatus_Exception(status)) status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) Py_ExitStatusException(status);
    PyObject *flags = PySys_GetObject("flags");
    const char *names[] = {"isolated", "no_site", "ignore_environment", "no_user_site"};
    for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
        PyObject *flag = PyObject_GetAttrString(flags, names[i]);
        int enabled = flag != NULL && PyObject_IsTrue(flag) == 1;
        Py_XDECREF(flag);
        if (!enabled) return 1;
    }
    const char *paths[] = {"/app/rule-runtime", "/app/rule-runtime/site-packages"};
    for (size_t i = 0; i < sizeof(paths) / sizeof(paths[0]); i++) {
        PyObject *path = PyUnicode_FromString(paths[i]);
        if (path == NULL || PyList_Append(PySys_GetObject("path"), path) < 0) {
            Py_XDECREF(path);
            PyErr_Print();
            return 1;
        }
        Py_DECREF(path);
    }
    if (argc == 2 && strcmp(argv[1], "--check-isolation") == 0) {
        puts("{\"isolated\":1,\"no_site\":1,\"ignore_environment\":1,\"no_user_site\":1}");
        return Py_FinalizeEx() < 0 ? 1 : 0;
    }
    PyObject *module = PyImport_ImportModule("enhanced_worker");
    PyObject *result = module == NULL ? NULL : PyObject_CallMethod(
        module, argc == 2 ? "describe_runtime" : "main", NULL);
    Py_XDECREF(module);
    if (result == NULL) {
        PyErr_Print();
        return 1;
    }
    if (argc == 2) {
        PyObject *json = PyImport_ImportModule("json");
        PyObject *encoded = json == NULL ? NULL : PyObject_CallMethod(json, "dumps", "O", result);
        Py_XDECREF(json);
        const char *text = encoded == NULL ? NULL : PyUnicode_AsUTF8(encoded);
        if (text == NULL) {
            Py_XDECREF(encoded);
            Py_DECREF(result);
            PyErr_Print();
            return 1;
        }
        puts(text);
        Py_DECREF(encoded);
    }
    Py_DECREF(result);
    return Py_FinalizeEx() < 0 ? 1 : 0;
}
