/* Hold one pylibseekdb instance for all processes in the container. */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <signal.h>
#include <stdio.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

static volatile sig_atomic_t stopping;

static void stop(int signal_number)
{
    (void)signal_number;
    stopping = 1;
}

static int close_handle(PyObject *handle)
{
    if (handle == NULL) {
        return 0;
    }
    PyObject *result = PyObject_CallMethod(handle, "close", NULL);
    if (result == NULL) {
        PyErr_Print();
        return -1;
    }
    Py_DECREF(result);
    return 0;
}

static int initialize_schemas(PyObject *instance)
{
    const char *statements[] = {
        "CREATE DATABASE IF NOT EXISTS aivision",
        "CREATE DATABASE IF NOT EXISTS signoz",
        "CREATE DATABASE IF NOT EXISTS agentlens",
    };
    PyObject *connection = PyObject_CallMethod(instance, "connect", "sO", "test", Py_True);
    PyObject *cursor = connection ? PyObject_CallMethod(connection, "cursor", NULL) : NULL;
    int result = -1;
    if (cursor == NULL) {
        PyErr_Print();
        goto finish;
    }
    for (size_t i = 0; i < sizeof(statements) / sizeof(statements[0]); i++) {
        PyObject *execution = PyObject_CallMethod(cursor, "execute", "s", statements[i]);
        if (execution == NULL) {
            PyErr_Print();
            goto finish;
        }
        Py_DECREF(execution);
    }
    result = 0;
finish:
    if (close_handle(cursor) < 0 || close_handle(connection) < 0) {
        result = -1;
    }
    Py_XDECREF(cursor);
    Py_XDECREF(connection);
    return result;
}

int main(int argc, char **argv)
{
    if (argc != 3) {
        fprintf(stderr, "usage: aivision-seekdb DATA_DIR SOCKET_ALIAS\n");
        return 2;
    }
    Py_Initialize();
    struct sigaction action = {.sa_handler = stop};
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGTERM, &action, NULL) || sigaction(SIGINT, &action, NULL)) {
        perror("set shutdown signal handlers");
        Py_FinalizeEx();
        return 1;
    }

    int status = 1;
    int alias_created = 0;
    PyObject *module = PyImport_ImportModule("pylibseekdb");
    PyObject *instance = module ? PyObject_CallMethod(module, "open", "s", argv[1]) : NULL;
    PyObject *options = instance ? PyObject_CallMethod(instance, "connection_options", NULL) : NULL;
    if (options == NULL) {
        PyErr_Print();
        goto finish;
    }
    PyObject *value = PyDict_GetItemString(options, "unix_socket");
    if (value == NULL) {
        fprintf(stderr, "pylibseekdb did not return a Unix socket\n");
        goto finish;
    }
    const char *socket = PyUnicode_AsUTF8(value);
    struct stat info;
    if (socket == NULL) {
        PyErr_Print();
        goto finish;
    }
    if (socket[0] != '/' || stat(socket, &info) != 0 || !S_ISSOCK(info.st_mode)) {
        fprintf(stderr, "pylibseekdb socket is unavailable\n");
        goto finish;
    }
    if (stopping) {
        status = 0;
        goto finish;
    }
    if (initialize_schemas(instance) < 0) {
        goto finish;
    }
    if (symlink(socket, argv[2]) != 0) {
        perror("publish local database socket");
        goto finish;
    }
    alias_created = 1;
    puts("seekdb: application schemas ready over Unix socket");
    fflush(stdout);

    const struct timespec interval = {.tv_sec = 0, .tv_nsec = 250000000};
    while (!stopping) {
        nanosleep(&interval, NULL);
    }
    status = 0;
finish:
    if (alias_created && unlink(argv[2]) != 0) {
        perror("remove local database socket alias");
        status = 1;
    }
    Py_XDECREF(options);
    if (close_handle(instance) < 0) {
        status = 1;
    }
    Py_XDECREF(instance);
    Py_XDECREF(module);
    if (Py_FinalizeEx() < 0) {
        status = 1;
    }
    return status;
}
