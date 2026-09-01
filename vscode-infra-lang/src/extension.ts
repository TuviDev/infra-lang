import * as vscode from 'vscode';
import {
    LanguageClient,
    LanguageClientOptions,
    ServerOptions,
    TransportKind,
} from 'vscode-languageclient/node';

let client: LanguageClient;

function codeLensSettings(): Record<string, unknown> {
    const cfg = vscode.workspace.getConfiguration('infra.codelens');
    return {
        'infra.codelens.enabled': cfg.get<boolean>('enabled', true),
        'infra.codelens.showCost': cfg.get<boolean>('showCost', true),
        'infra.codelens.showSecurity': cfg.get<boolean>('showSecurity', true),
        'infra.codelens.showReliability': cfg.get<boolean>('showReliability', true),
        'infra.codelens.emoji': cfg.get<string>('emoji', 'auto'),
    };
}

export function activate(context: vscode.ExtensionContext): void {
    const pythonPath = vscode.workspace
        .getConfiguration('python')
        .get<string>('defaultInterpreterPath', 'python');

    const serverOptions: ServerOptions = {
        command: pythonPath,
        args: ['-m', 'infra.lsp.server'],
        transport: TransportKind.stdio,
    };

    const clientOptions: LanguageClientOptions = {
        documentSelector: [{ scheme: 'file', language: 'infra' }],
        synchronize: {
            fileEvents: vscode.workspace.createFileSystemWatcher('**/*.infra'),
        },
        initializationOptions: codeLensSettings(),
    };

    client = new LanguageClient(
        'infra-lang',
        'Infra Lang',
        serverOptions,
        clientOptions,
    );

    client.start();
    context.subscriptions.push(client);

    // Live-reload CodeLens settings: forward changes to the server so
    // freshly computed lenses pick them up without an extension restart.
    context.subscriptions.push(
        vscode.workspace.onDidChangeConfiguration((event) => {
            if (event.affectsConfiguration('infra.codelens') && client) {
                client.sendNotification('workspace/didChangeConfiguration', {
                    settings: codeLensSettings(),
                });
            }
        }),
    );
}

export function deactivate(): Promise<void> | undefined {
    return client?.stop();
}
