# -*- coding: utf-8 -*-

from frida.application import await_enter
from frida.core import ModuleFunction
import threading


class Discoverer(object):
    def __init__(self, reactor):
        self._reactor = reactor
        self._script = None

    def dispose(self):
        if self._script is not None:
            try:
                self._script.unload()
            except:
                pass
            self._script = None

    def start(self, process, ui):
        def on_message(message, data):
            self._reactor.schedule(lambda: self._process_message(message, data, process, ui))
        source = self._create_discover_script()
        self._script = process.session.create_script(source)
        self._script.on('message', on_message)
        self._script.load()

    def stop(self):
        self._script.post_message({
            'to': "/sampler",
            'name': '+stop',
            'payload': {}
        })

    def _create_discover_script(self):
        return """\
function Sampler() {
    var threadIds = [];
    var result = {};

    function onStanza(stanza) {
        if (stanza.to === "/sampler") {
            if (stanza.name === '+stop') {
                stop();
            }
        }

        recv(onStanza);
    }

    this.start = function () {
        threadIds = [];
        Process.enumerateThreads({
            onMatch: function (thread) {
                threadIds.push(thread.id);
            },
            onComplete: function () {
                threadIds.forEach(function (threadId) {
                    Stalker.follow(threadId, {
                        events: { call: true },
                        onCallSummary: function (summary) {
                            for (var address in summary) {
                                if (summary.hasOwnProperty(address)) {
                                    var count = result[address] || 0;
                                    result[address] = count + summary[address];
                                }
                            }
                        }
                    });
                });

                send({
                    from: "/sampler",
                    name: '+started',
                    payload: {
                        total: threadIds.length
                    }
                });
            }
        });
    }

    function stop() {
        threadIds.forEach(function (threadId) {
            Stalker.unfollow(threadId);
        });
        threadIds = [];

        send({
            from: "/sampler",
            name: '+stopped',
            payload: {
                result: result
            }
        });
        result = {};
    }

    recv(onStanza);
};

sampler = new Sampler();
setTimeout(function () { sampler.start(); }, 0);
"""

    def _process_message(self, message, data, process, ui):
        if message['type'] == 'send':
            stanza = message['payload']
            name = stanza['name']
            payload = stanza['payload']
            if stanza['from'] == "/sampler":
                if name == '+started':
                    ui.on_sample_start(payload['total'])
                elif name == '+stopped':
                    module_functions = {}
                    dynamic_functions = []
                    for address, count in payload['result'].items():
                        address = int(address, 16)
                        function = process.ensure_function(address)
                        if isinstance(function, ModuleFunction):
                            functions = module_functions.get(function.module, [])
                            if len(functions) == 0:
                                module_functions[function.module] = functions
                            functions.append((function, count))
                        else:
                            dynamic_functions.append((function, count))
                    ui.on_sample_result(module_functions, dynamic_functions)
                else:
                    print(message, data)
            else:
                print(message, data)
        else:
            print(message, data)

class UI(object):
    def on_sample_start(self, total):
        pass

    def on_sample_result(self, module_functions, dynamic_functions):
        pass


def main():
    from frida.application import ConsoleApplication

    class DiscovererApplication(ConsoleApplication, UI):
        def __init__(self):
            self._results_received = threading.Event()
            ConsoleApplication.__init__(self, self._await_keys)

        def _await_keys(self):
            await_enter()
            self._reactor.schedule(lambda: self._discoverer.stop())
            self._results_received.wait()

        def _usage(self):
            return "usage: %prog [options] target"

        def _initialize(self, parser, options, args):
            self._discoverer = None

        def _needs_target(self):
            return True

        def _start(self):
            self._update_status("Injecting script...")
            self._discoverer = Discoverer(self._reactor)
            self._discoverer.start(self._process, self)

        def _stop(self):
            print("Stopping...")
            self._discoverer.dispose()
            self._discoverer = None

        def on_sample_start(self, total):
            self._update_status("Tracing %d threads. Press ENTER to stop." % total)
            self._resume()

        def on_sample_result(self, module_functions, dynamic_functions):
            for module, functions in module_functions.items():
                print(module.name)
                print("\t%-10s\t%s" % ("Calls", "Function"))
                for function, count in sorted(functions, key=lambda item: item[1], reverse=True):
                    print("\t%-10d\t%s" % (count, function))
                print("")

            if len(dynamic_functions) > 0:
                print("Dynamic functions:")
                print("\t%-10s\t%s" % ("Calls", "Function"))
                for function, count in sorted(dynamic_functions, key=lambda item: item[1], reverse=True):
                    print("\t%-10d\t%s" % (count, function))

            self._results_received.set()

    app = DiscovererApplication()
    app.run()


if __name__ == '__main__':
    main()
