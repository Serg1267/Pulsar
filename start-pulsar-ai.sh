#!/bin/bash

echo "🚀 Запуск системы Pulsar + ИИ..."
echo ""

# Переходим в папку Pulsar
cd ~/Pulsar

# Проверяем, не запущен ли уже сервер
if lsof -Pi :8000 -sTCP:LISTEN -t >/dev/null ; then
    echo "⚠️  SPICEBridge уже запущен на порту 8000"
else
    echo "🌉 Запускаю SPICEBridge в фоне..."
    uvx spicebridge --transport sse --port 8000 > /dev/null 2>&1 &
    SERVER_PID=$!
    sleep 2
    
    if kill -0 $SERVER_PID 2>/dev/null; then
        echo "✅ SPICEBridge запущен (PID: $SERVER_PID)"
    else
        echo "❌ Ошибка запуска SPICEBridge"
        exit 1
    fi
fi

echo ""
echo "📁 Структура проекта:"
echo "   Модели: ~/Pulsar/Mod/"
echo "   Промпты: ~/Pulsar/prompts/"
echo "   Skills: ~/Pulsar/skills/"
echo "   Тесты: ~/Pulsar/tests/"
echo ""
echo "🧠 Запускаю OpenCode..."
echo "   Подсказка: используй промпты из ~/Pulsar/prompts/"
echo ""

opencode

# Остановка сервера после выхода из OpenCode
if [ ! -z "$SERVER_PID" ]; then
    echo ""
    echo "🛑 Останавливаю SPICEBridge..."
    kill $SERVER_PID 2>/dev/null
    echo "✅ Готово!"
fi
